#!/usr/bin/env python3
"""TinyTuya Vacuum Controller - HA Add-on REST API
Local control for Eufy Robovac S1 Pro (T2080A)
"""
import os, json, time, logging
from flask import Flask, jsonify
import tinytuya

DEVICE_ID = os.environ.get('DEVICE_ID', '')
LOCAL_KEY = os.environ.get('LOCAL_KEY', '')
DEVICE_IP = os.environ.get('DEVICE_IP', '')
PROTOCOL = float(os.environ.get('PROTOCOL', '3.3'))
API_PORT = int(os.environ.get('API_PORT', '8099'))

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('tinytuya-vacuum')

WORK_STATUS = {0:'standby',1:'cleaning',2:'paused',5:'returning',34:'docked'}
SUCTION_LEVELS = ['Quiet','Standard','Turbo','Max']

def get_device():
    d = tinytuya.Device(DEVICE_ID, DEVICE_IP, LOCAL_KEY, version=PROTOCOL)
    d.set_socketTimeout(5)
    d.set_socketRetryLimit(2)
    return d

@app.route('/')
def index():
    return jsonify({
        'name': 'TinyTuya Vacuum Controller',
        'version': '0.2.0',
        'device_id': DEVICE_ID,
        'device_ip': DEVICE_IP,
        'endpoints': ['/health','/status','/start','/dock','/suction/<level>','/find']
    })

@app.route('/health')
def health():
    return jsonify({'status':'ok','device_id':DEVICE_ID,'device_ip':DEVICE_IP})

@app.route('/status')
def status():
    try:
        d = get_device()
        raw = d.status()
        dps = raw.get('dps',{})
        wc = dps.get('6',-1)
        return jsonify({
            'online':True,
            'state':WORK_STATUS.get(wc,f'unknown_{wc}'),
            'battery':dps.get('8',0),
            'suction':dps.get('158','?'),
            'clean_mode':dps.get('9','?'),
            'water_level':dps.get('10','?'),
            'mop':dps.get('40','?'),
            'power':dps.get('156',False),
            'raw_dps':dps,
            'timestamp':time.time()
        })
    except Exception as e:
        logger.error(f'Status error: {e}')
        return jsonify({'online':False,'error':str(e),'timestamp':time.time()}),500

@app.route('/start', methods=['POST','GET'])
def start():
    try:
        d = get_device()
        r = d.set_value(160, True)
        logger.info(f'Start command sent: {r}')
        return jsonify({'success':True,'message':'Start command sent (~5-8 min delay for map processing)'})
    except Exception as e:
        logger.error(f'Start error: {e}')
        return jsonify({'success':False,'error':str(e)}),500

@app.route('/dock', methods=['POST','GET'])
def dock():
    try:
        d = get_device()
        d.set_value(160, False)
        return jsonify({'success':True,'message':'Dock command sent'})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}),500

@app.route('/suction/<level>', methods=['POST','GET'])
def suction(level):
    m = {'quiet':'Quiet','standard':'Standard','turbo':'Turbo','max':'Max'}
    target = m.get(level.lower(), level)
    if target not in SUCTION_LEVELS:
        return jsonify({'success':False,'error':f'Use: {SUCTION_LEVELS}'}),400
    try:
        d = get_device()
        d.set_value(158, target)
        time.sleep(2)
        s = d.status()
        actual = s.get('dps',{}).get('158','?')
        return jsonify({'success':actual==target,'suction':actual})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}),500

@app.route('/find', methods=['POST','GET'])
def find():
    try:
        d = get_device()
        d.set_value(159, False)
        time.sleep(1)
        d.set_value(159, True)
        return jsonify({'success':True,'message':'Find command sent'})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}),500

if __name__ == '__main__':
    logger.info(f'TinyTuya Vacuum Controller v0.2.0')
    logger.info(f'Device: {DEVICE_ID} at {DEVICE_IP} (protocol {PROTOCOL})')
    logger.info(f'Listening on port {API_PORT}')
    app.run(host='0.0.0.0', port=API_PORT, debug=False)
