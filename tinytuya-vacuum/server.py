#!/usr/bin/env python3
"""TinyTuya Vacuum Controller v3.0.0 — HA Add-on
Local Tuya control for Eufy robot vacuums with Event Bus SSE integration.

v3.0 additions:
  - Bedroom safety: if bedroom motion in last 30min AND before 9am, defer vacuum start
  - Bedroom/Echo entity constants for cross-addon safety
  - Silent hours awareness (22:00-08:00)

v2.0 additions:
  - Event Bus SSE subscriber: event-driven departure start (no cron delay)
  - Cleaning pattern analytics: days between cleans, duration trends, battery drain
  - Auto-suggest: notifies after N days without cleaning
  - Post-clean report pushed when vacuum docks after a session
  - Cooper-aware: won't start while Cooper is home (checks Intelligence)
  - Persistent patterns in /data/vacuum_v2.json

Endpoints:
  GET  /health, /status, /history, /dps
  POST /start, /dock, /pause, /resume
  POST /suction/<level>, /water/<level>, /find
  GET  /patterns  — Cleaning pattern analytics
  GET  /suggest   — Should we clean? Smart suggestion
  GET  /event-log — Recent event-driven actions
"""
import os, json, time, logging, threading
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, jsonify, request
import requests as http
import tinytuya
import sseclient

DEVICE_ID = os.environ.get('DEVICE_ID', '')
LOCAL_KEY = os.environ.get('LOCAL_KEY', '')
DEVICE_IP = os.environ.get('DEVICE_IP', '')
PROTOCOL = float(os.environ.get('PROTOCOL', '3.3'))
API_PORT = int(os.environ.get('API_PORT', '8099'))
HISTORY_MAX = int(os.environ.get('HISTORY_MAX', '50'))
EVENT_BUS_URL = os.environ.get('EVENT_BUS_URL', 'http://localhost:8092')
INTELLIGENCE_URL = os.environ.get('INTELLIGENCE_URL', 'http://localhost:8093')
HA_URL = os.environ.get('HA_URL', 'http://localhost:8123')
HA_TOKEN = os.environ.get('HA_TOKEN', '')
AUTO_CLEAN_DAYS = int(os.environ.get('AUTO_CLEAN_DAYS', '2'))

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('tinytuya-vacuum')

# v3.0: Safety constants
BEDROOM_ENTITIES = lambda eid: 'bedroom' in eid.lower()
ECHO_ENTITIES = [
    'media_player.living_room_echo_show',
    'media_player.kitchen_echo_show',
    'media_player.bedroom_echo',
]
SILENT_HOURS = lambda: datetime.now().hour >= 22 or datetime.now().hour < 8

# v3.0: Track bedroom motion time for vacuum deferral
last_bedroom_motion_time = None

def is_bedroom_safe():
    """v3.0: Check if bedroom motion is active."""
    try:
        r = http.get(f'{HA_URL}/api/states/binary_sensor.bedroom_motion', headers={'Authorization': f'Bearer {HA_TOKEN}'}, timeout=5)
        if r.status_code == 200: return r.json().get('state') == 'on'
    except: pass
    return False

def should_defer_vacuum():
    """v3.0: Defer vacuum if bedroom motion in last 30min AND before 9am."""
    now = datetime.now()
    if now.hour >= 9:
        return False  # After 9am, no deferral needed
    # Check if bedroom motion was recent
    if last_bedroom_motion_time:
        age_min = (now - last_bedroom_motion_time).total_seconds() / 60
        if age_min < 30:
            logger.info(f'DEFER: Bedroom motion {age_min:.0f}min ago and before 9am — deferring vacuum')
            return True
    # Also check live state
    if is_bedroom_safe():
        logger.info('DEFER: Bedroom motion active and before 9am — deferring vacuum')
        return True
    return False

WORK_STATUS = {0: 'standby', 1: 'cleaning', 2: 'paused', 5: 'returning', 34: 'docked'}
SUCTION_LEVELS = ['Quiet', 'Standard', 'Turbo', 'Max']
WATER_LEVELS = ['low', 'middle', 'high']

last_status = {}
status_lock = threading.Lock()
cleaning_history = []
current_session = None
HISTORY_FILE = '/data/cleaning_history.json'
PATTERNS_FILE = '/data/vacuum_v2.json'

event_actions = deque(maxlen=100)
post_clean_reported = False


def load_history():
    global cleaning_history
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE) as f:
                cleaning_history = json.load(f)
            logger.info(f'Loaded {len(cleaning_history)} history entries')
    except: pass


def save_history():
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(cleaning_history[-HISTORY_MAX:], f, indent=2)
    except: pass


def get_device():
    d = tinytuya.Device(DEVICE_ID, DEVICE_IP, LOCAL_KEY, version=PROTOCOL)
    d.set_socketTimeout(5)
    d.set_socketRetryLimit(2)
    return d


def get_status_data():
    try:
        d = get_device()
        raw = d.status()
        dps = raw.get('dps', {})
        wc = dps.get('6', -1)
        data = {
            'online': True,
            'state': WORK_STATUS.get(wc, f'unknown_{wc}'),
            'state_code': wc,
            'battery': dps.get('8', 0),
            'suction': dps.get('158', '?'),
            'clean_mode': dps.get('9', '?'),
            'water_level': dps.get('10', '?'),
            'mop': dps.get('40', '?'),
            'power': dps.get('156', False),
            'is_cleaning': wc == 1,
            'is_docked': wc == 34 or wc == 0,
            'raw_dps': dps,
            'timestamp': time.time(),
        }
        with status_lock:
            last_status.update(data)
        return data
    except Exception as e:
        logger.error(f'Status error: {e}')
        return {'online': False, 'error': str(e), 'timestamp': time.time()}


def days_since_last_clean():
    if not cleaning_history: return 999
    last = cleaning_history[-1]
    last_end = last.get('end', last.get('start', ''))
    if not last_end: return 999
    try:
        last_dt = datetime.fromisoformat(last_end)
        return (datetime.now() - last_dt).total_seconds() / 86400
    except: return 999


def compute_patterns():
    if len(cleaning_history) < 2:
        return {'sessions': len(cleaning_history), 'insufficient_data': True}
    durations = [s.get('duration_min', 0) for s in cleaning_history if s.get('duration_min')]
    batteries_start = [s.get('battery_start', 0) for s in cleaning_history if s.get('battery_start')]
    batteries_end = [s.get('battery_end', 0) for s in cleaning_history if s.get('battery_end')]
    gaps = []
    for i in range(1, len(cleaning_history)):
        try:
            t1 = datetime.fromisoformat(cleaning_history[i - 1].get('end', cleaning_history[i - 1]['start']))
            t2 = datetime.fromisoformat(cleaning_history[i]['start'])
            gaps.append((t2 - t1).total_seconds() / 86400)
        except: pass
    return {
        'sessions': len(cleaning_history),
        'avg_duration_min': round(sum(durations) / len(durations), 1) if durations else None,
        'avg_gap_days': round(sum(gaps) / len(gaps), 1) if gaps else None,
        'avg_battery_drain': round(sum(s - e for s, e in zip(batteries_start, batteries_end)) / len(batteries_start), 1) if batteries_start and batteries_end else None,
        'days_since_last': round(days_since_last_clean(), 1),
        'last_session': cleaning_history[-1] if cleaning_history else None,
    }


def ha_notify(title, msg):
    try:
        http.post(f'{HA_URL}/api/services/notify/mobile_app_bks_home_assistant_chatsworth',
            headers={'Authorization': f'Bearer {HA_TOKEN}', 'Content-Type': 'application/json'},
            json={'data': {'title': title, 'message': msg}}, timeout=5)
    except: pass


def is_cooper_here():
    try:
        r = http.get(f'{INTELLIGENCE_URL}/cooper', timeout=3)
        if r.status_code == 200: return r.json().get('here', False)
    except: pass
    return False


def handle_event(ev):
    global post_clean_reported, last_bedroom_motion_time
    eid = ev.get('entity_id', '')
    new = ev.get('new_state', '')
    old = ev.get('old_state', '')
    sig = ev.get('significant', False)
    action = None

    # v3.0: Track bedroom motion for vacuum deferral
    if 'bedroom' in eid and 'motion' in eid and new == 'on':
        last_bedroom_motion_time = datetime.now()

    # Departure event — start vacuum immediately
    if 'presence' in eid and new == 'off' and old == 'on':
        if is_cooper_here():
            logger.info('EVENT: Departure detected but Cooper is home — skipping vacuum')
            action = 'departure_skipped_cooper'
        elif should_defer_vacuum():
            logger.info('EVENT: Departure detected but bedroom motion recent + early — deferring')
            ha_notify('\U0001f916 Vacuum Deferred', 'Someone may be sleeping. Will start later.')
            action = 'departure_deferred_bedroom'
        else:
            logger.info('EVENT: Departure detected — starting vacuum immediately')
            try:
                d = get_device()
                d.set_value(160, True)
                action = 'departure_start'
                ha_notify('\U0001f916 Vacuum Started', 'Cleaning started automatically after departure.')
            except Exception as e:
                logger.error(f'Auto-start failed: {e}')
                action = f'departure_start_failed: {e}'

    # Arrival — dock if cleaning
    elif 'presence' in eid and new == 'on' and old == 'off':
        with status_lock:
            state = last_status.get('state', 'unknown')
        if state == 'cleaning':
            logger.info('EVENT: Arrival detected while cleaning — sending dock')
            try:
                d = get_device()
                d.set_value(160, False)
                action = 'arrival_dock'
            except Exception as e:
                action = f'arrival_dock_failed: {e}'
        else:
            action = 'arrival_noted'

    # Vacuum finished — post-clean report
    elif 'vacuum' in eid and new in ['docked', 'standby'] and old in ['cleaning', 'returning']:
        if not post_clean_reported:
            patterns = compute_patterns()
            msg = f"Clean complete. Duration: {patterns.get('avg_duration_min', '?')}min avg. Last: {patterns.get('days_since_last', '?')} days ago."
            ha_notify('\U0001f9f9 Clean Complete', msg)
            post_clean_reported = True
            action = 'post_clean_report'
    elif 'vacuum' in eid and new == 'cleaning':
        post_clean_reported = False
        action = 'cleaning_started_noted'

    if action:
        event_actions.append({'time': datetime.now().isoformat(), 'event': eid, 'action': action, 'old': old, 'new': new})
        logger.info(f'ACTION: {action}')


def event_bus_subscriber():
    while True:
        try:
            logger.info(f'Connecting to Event Bus SSE: {EVENT_BUS_URL}/events/stream')
            response = http.get(f'{EVENT_BUS_URL}/events/stream', stream=True, timeout=None)
            client = sseclient.SSEClient(response)
            logger.info('Event Bus SSE connected')
            for event in client.events():
                try:
                    ev = json.loads(event.data)
                    handle_event(ev)
                except json.JSONDecodeError: pass
                except Exception as e: logger.error(f'Event handling error: {e}')
        except Exception as e:
            logger.error(f'Event Bus SSE disconnected: {e}')
        logger.info('Reconnecting to Event Bus in 10s...')
        time.sleep(10)


def auto_suggest_loop():
    while True:
        time.sleep(3600)
        days = days_since_last_clean()
        if days >= AUTO_CLEAN_DAYS:
            hour = datetime.now().hour
            if 9 <= hour <= 20:
                ha_notify('\U0001f916 Vacuum Suggestion', f"It's been {days:.1f} days since the last clean. Start when you leave next?")
                logger.info(f'Auto-suggest: {days:.1f} days since last clean')


def track_cleaning():
    global current_session
    while True:
        try:
            data = get_status_data()
            state = data.get('state', 'unknown')
            if state == 'cleaning' and current_session is None:
                current_session = {'start': datetime.now().isoformat(), 'suction': data.get('suction', '?'), 'battery_start': data.get('battery', 0)}
                logger.info('Cleaning session started')
            elif state in ['docked', 'standby'] and current_session is not None:
                current_session['end'] = datetime.now().isoformat()
                current_session['battery_end'] = data.get('battery', 0)
                current_session['duration_min'] = round((time.time() - datetime.fromisoformat(current_session['start']).timestamp()) / 60, 1)
                cleaning_history.append(current_session)
                save_history()
                logger.info(f'Cleaning session ended: {current_session["duration_min"]}min')
                current_session = None
        except: pass
        time.sleep(30)


@app.route('/')
def index():
    return jsonify({
        'name': 'TinyTuya Vacuum Controller', 'version': '3.0.0',
        'device_id': DEVICE_ID, 'device_ip': DEVICE_IP,
        'days_since_last_clean': round(days_since_last_clean(), 1),
        'endpoints': ['/health', '/status', '/start', '/dock', '/pause', '/resume',
                      '/suction/<level>', '/water/<level>', '/find', '/history',
                      '/dps', '/patterns', '/suggest', '/event-log'],
        'suction_levels': SUCTION_LEVELS, 'water_levels': WATER_LEVELS,
    })

@app.route('/health')
def health():
    with status_lock:
        age = round(time.time() - last_status.get('timestamp', 0)) if last_status else -1
    return jsonify({'status': 'ok', 'device_id': DEVICE_ID, 'last_poll_age_seconds': age,
        'sessions_tracked': len(cleaning_history), 'event_bus': 'connected' if event_actions else 'waiting',
        'days_since_last_clean': round(days_since_last_clean(), 1)})

@app.route('/status')
def status():
    with status_lock:
        if last_status and (time.time() - last_status.get('timestamp', 0)) < 15:
            return jsonify({**last_status, 'source': 'cache'})
    data = get_status_data()
    return jsonify({**data, 'source': 'live'})

@app.route('/start', methods=['POST', 'GET'])
def start():
    # v3.0: Check bedroom deferral
    if should_defer_vacuum():
        return jsonify({'success': False, 'deferred': True, 'reason': 'Bedroom motion recent + before 9am — someone may be sleeping'})
    try:
        d = get_device()
        r = d.set_value(160, True)
        logger.info(f'Start: {r}')
        return jsonify({'success': True, 'message': 'Start command sent (~5-8 min delay for map processing)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dock', methods=['POST', 'GET'])
def dock():
    try:
        d = get_device()
        d.set_value(160, False)
        return jsonify({'success': True, 'message': 'Dock command sent'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/pause', methods=['POST', 'GET'])
def pause():
    try:
        d = get_device()
        d.set_value(160, False)
        return jsonify({'success': True, 'message': 'Pause sent (same as dock for S1 Pro)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/resume', methods=['POST', 'GET'])
def resume():
    try:
        d = get_device()
        d.set_value(160, True)
        return jsonify({'success': True, 'message': 'Resume sent'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/suction/<level>', methods=['POST', 'GET'])
def suction(level):
    m = {'quiet': 'Quiet', 'q': 'Quiet', 'standard': 'Standard', 's': 'Standard', 'turbo': 'Turbo', 't': 'Turbo', 'max': 'Max', 'm': 'Max'}
    target = m.get(level.lower(), level)
    if target not in SUCTION_LEVELS: return jsonify({'success': False, 'error': f'Use: {SUCTION_LEVELS}'}), 400
    try:
        d = get_device()
        d.set_value(158, target)
        time.sleep(2)
        s = d.status()
        actual = s.get('dps', {}).get('158', '?')
        return jsonify({'success': actual == target, 'suction': actual, 'requested': target})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/water/<level>', methods=['POST', 'GET'])
def water(level):
    if level.lower() not in WATER_LEVELS: return jsonify({'success': False, 'error': f'Use: {WATER_LEVELS}'}), 400
    try:
        d = get_device()
        d.set_value(10, level.lower())
        time.sleep(2)
        s = d.status()
        actual = s.get('dps', {}).get('10', '?')
        return jsonify({'success': True, 'water_level': actual})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/find', methods=['POST', 'GET'])
def find():
    try:
        d = get_device()
        d.set_value(159, False)
        time.sleep(1)
        d.set_value(159, True)
        return jsonify({'success': True, 'message': 'Find command sent'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/history')
def history():
    limit = request.args.get('limit', 10, type=int)
    return jsonify({'total_sessions': len(cleaning_history), 'sessions': cleaning_history[-limit:], 'current_session': current_session})

@app.route('/dps')
def raw_dps():
    try:
        d = get_device()
        raw = d.status()
        return jsonify(raw)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/patterns')
def patterns():
    return jsonify(compute_patterns())

@app.route('/suggest')
def suggest():
    days = days_since_last_clean()
    patterns_data = compute_patterns()
    avg_gap = patterns_data.get('avg_gap_days', AUTO_CLEAN_DAYS)
    should_clean = days >= (avg_gap or AUTO_CLEAN_DAYS)
    return jsonify({'should_clean': should_clean, 'days_since_last': round(days, 1), 'avg_gap_days': avg_gap,
        'message': f"It's been {days:.1f} days. {'Time to clean!' if should_clean else 'Not due yet.'}"})

@app.route('/event-log')
def event_log():
    return jsonify(list(event_actions)[-20:])

if __name__ == '__main__':
    logger.info('TinyTuya Vacuum Controller v3.0.0')
    logger.info(f'Device: {DEVICE_ID} at {DEVICE_IP} (protocol {PROTOCOL})')
    logger.info(f'Listening on port {API_PORT}')
    load_history()
    threading.Thread(target=track_cleaning, daemon=True).start()
    threading.Thread(target=event_bus_subscriber, daemon=True).start()
    threading.Thread(target=auto_suggest_loop, daemon=True).start()
    logger.info('Cleaning tracker + Event Bus subscriber + auto-suggest started')
    app.run(host='0.0.0.0', port=API_PORT, debug=False)
