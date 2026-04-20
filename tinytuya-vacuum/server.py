#!/usr/bin/env python3
"""TinyTuya Vacuum Controller v1.0.0 — HA Add-on
Local Tuya control for Eufy robot vacuums (S1 Pro T2080A and compatible).

Endpoints:
  GET  /health              — Health check
  GET  /status              — Full vacuum status
  POST /start               — Start cleaning
  POST /dock                — Return to dock
  POST /suction/<level>     — Set suction (Quiet/Standard/Turbo/Max)
  POST /find                — Locate vacuum (beep)
  POST /water/<level>       — Set water level (low/middle/high)
  GET  /history             — Cleaning history
  GET  /dps                 — Raw DPS values (debug)
  POST /pause               — Pause cleaning
  POST /resume              — Resume cleaning
"""
import os, json, time, logging, threading
from datetime import datetime
from flask import Flask, jsonify, request
import tinytuya

DEVICE_ID = os.environ.get('DEVICE_ID', '')
LOCAL_KEY = os.environ.get('LOCAL_KEY', '')
DEVICE_IP = os.environ.get('DEVICE_IP', '')
PROTOCOL = float(os.environ.get('PROTOCOL', '3.3'))
API_PORT = int(os.environ.get('API_PORT', '8099'))
HISTORY_MAX = int(os.environ.get('HISTORY_MAX', '50'))

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('tinytuya-vacuum')

WORK_STATUS = {0:'standby',1:'cleaning',2:'paused',5:'returning',34:'docked'}
SUCTION_LEVELS = ['Quiet','Standard','Turbo','Max']
WATER_LEVELS = ['low','middle','high']

# State tracking
last_status = {}
status_lock = threading.Lock()
cleaning_history = []
current_session = None
HISTORY_FILE = '/data/cleaning_history.json'

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

def track_cleaning():
    """Background thread: track cleaning sessions for history."""
    global current_session
    while True:
        try:
            data = get_status_data()
            state = data.get('state', 'unknown')
            
            # Start of cleaning
            if state == 'cleaning' and current_session is None:
                current_session = {
                    'start': datetime.now().isoformat(),
                    'suction': data.get('suction', '?'),
                    'battery_start': data.get('battery', 0),
                }
                logger.info('Cleaning session started')
            
            # End of cleaning
            elif state in ['docked', 'standby'] and current_session is not None:
                current_session['end'] = datetime.now().isoformat()
                current_session['battery_end'] = data.get('battery', 0)
                current_session['duration_min'] = round(
                    (time.time() - datetime.fromisoformat(current_session['start']).timestamp()) / 60, 1
                )
                cleaning_history.append(current_session)
                save_history()
                logger.info(f'Cleaning session ended: {current_session["duration_min"]}min')
                current_session = None
        except:
            pass
        time.sleep(30)

@app.route('/')
def index():
    return jsonify({
        'name': 'TinyTuya Vacuum Controller',
        'version': '1.0.0',
        'device_id': DEVICE_ID,
        'device_ip': DEVICE_IP,
        'endpoints': ['/health','/status','/start','/dock','/pause','/resume',
                      '/suction/<level>','/water/<level>','/find','/history','/dps'],
        'suction_levels': SUCTION_LEVELS,
        'water_levels': WATER_LEVELS,
    })

@app.route('/health')
def health():
    with status_lock:
        age = round(time.time() - last_status.get('timestamp', 0)) if last_status else -1
    return jsonify({'status': 'ok', 'device_id': DEVICE_ID, 'last_poll_age_seconds': age, 'sessions_tracked': len(cleaning_history)})

@app.route('/status')
def status():
    # Return cached if fresh enough
    with status_lock:
        if last_status and (time.time() - last_status.get('timestamp', 0)) < 15:
            return jsonify({**last_status, 'source': 'cache'})
    data = get_status_data()
    return jsonify({**data, 'source': 'live'})

@app.route('/start', methods=['POST','GET'])
def start():
    try:
        d = get_device()
        r = d.set_value(160, True)
        logger.info(f'Start: {r}')
        return jsonify({'success': True, 'message': 'Start command sent (~5-8 min delay for map processing)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/dock', methods=['POST','GET'])
def dock():
    try:
        d = get_device()
        d.set_value(160, False)
        return jsonify({'success': True, 'message': 'Dock command sent'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/pause', methods=['POST','GET'])
def pause():
    try:
        d = get_device()
        d.set_value(160, False)
        return jsonify({'success': True, 'message': 'Pause sent (same as dock for S1 Pro)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/resume', methods=['POST','GET'])
def resume():
    try:
        d = get_device()
        d.set_value(160, True)
        return jsonify({'success': True, 'message': 'Resume sent'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/suction/<level>', methods=['POST','GET'])
def suction(level):
    m = {'quiet':'Quiet','q':'Quiet','standard':'Standard','s':'Standard','turbo':'Turbo','t':'Turbo','max':'Max','m':'Max'}
    target = m.get(level.lower(), level)
    if target not in SUCTION_LEVELS:
        return jsonify({'success': False, 'error': f'Use: {SUCTION_LEVELS}'}), 400
    try:
        d = get_device()
        d.set_value(158, target)
        time.sleep(2)
        s = d.status()
        actual = s.get('dps', {}).get('158', '?')
        return jsonify({'success': actual == target, 'suction': actual, 'requested': target})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/water/<level>', methods=['POST','GET'])
def water(level):
    if level.lower() not in WATER_LEVELS:
        return jsonify({'success': False, 'error': f'Use: {WATER_LEVELS}'}), 400
    try:
        d = get_device()
        d.set_value(10, level.lower())
        time.sleep(2)
        s = d.status()
        actual = s.get('dps', {}).get('10', '?')
        return jsonify({'success': True, 'water_level': actual})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/find', methods=['POST','GET'])
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
    return jsonify({
        'total_sessions': len(cleaning_history),
        'sessions': cleaning_history[-limit:],
        'current_session': current_session,
    })

@app.route('/dps')
def raw_dps():
    try:
        d = get_device()
        raw = d.status()
        return jsonify(raw)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    logger.info(f'TinyTuya Vacuum Controller v1.0.0')
    logger.info(f'Device: {DEVICE_ID} at {DEVICE_IP} (protocol {PROTOCOL})')
    logger.info(f'Listening on port {API_PORT}')
    load_history()
    # Start background tracking
    tracker = threading.Thread(target=track_cleaning, daemon=True)
    tracker.start()
    logger.info('Cleaning session tracker started')
    app.run(host='0.0.0.0', port=API_PORT, debug=False)
