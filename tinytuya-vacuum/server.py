#!/usr/bin/env python3
"""TinyTuya Vacuum Controller - HA Add-on REST API"""
import os, json, time, logging
from flask import Flask, jsonify
import tinytuya

DEVICE_ID = os.environ.get('DEVICE_ID', '')
LOCAL_KEY = os.environ.get('LOCAL_KEY', '')
DEVICE_IP = os.environ.get('DEVICE_IP', '')
PROTOCOL = float(os.environ.get('PROTOCOL', '3.3'))
API_PORT = int(os.environ.get('API_PORT', '8099'))

app = Flask(__name__)
logger = logging.getLogger('tinytuya-vacuum')
logging.basicConfig(level=logging.INFO)

WORK_STATUS = {0:'standby',1:'cleaning',2:'paused',5:'returning',34:'docked'}
SUCTION_LEVELS = ['Quiet','Standard','Turbo','Max']

def dev():
    d = tinytuya.Device(DEVICE_ID, DEVICE_IP, LOCAL_KEY, version=PROTOCOL)
    d.set_socketTimeout(5)
    d.set_socketRetryLimit(2)
    return d

@app.route('/health')
def health():
    return jsonify({'status':'ok','device_id':DEVICE_ID,'device_ip':DEVICE_IP})

@app.route('/status')
def status():
    try:
        d = dev()
        raw = d.status()
        dps = raw.get('dps',{})
        wc = dps.get('6',-1)
        return jsonify({'online':True,'state':WORK_STATUS.get(wc,f'unknown_{wc}'),'battery':dps.get('8',0),'suction':dps.get('158','?'),'clean_mode':dps.get('9','?'),'water_level':dps.get('10','?'),'mop':dps.get('40','?'),'power':dps.get('156',False),'timestamp':time.time()})
    except Exception as e:
        return jsonify({'online':False,'error':str(e)}),500

@app.route('/start', methods=['POST'])
def start():
    try:
        d = dev()
        r = d.set_value(160, True)
        logger.info(f'Start sent: {r}')
        return jsonify({'success':True,'message':'Start command sent (~5-8 min delay)'})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}),500

@app.route('/dock', methods=['POST'])
def dock():
    try:
        d = dev()
        d.set_value(160, False)
        return jsonify({'success':True,'message':'Dock command sent'})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}),500

@app.route('/suction/<level>', methods=['POST'])
def suction(level):
    m = {'quiet':'Quiet','standard':'Standard','turbo':'Turbo','max':'Max'}
    target = m.get(level.lower(), level)
    if target not in SUCTION_LEVELS:
        return jsonify({'success':False,'error':f'Use: {SUCTION_LEVELS}'}),400
    try:
        d = dev()
        d.set_value(158, target)
        time.sleep(2)
        s = d.status()
        actual = s.get('dps',{}).get('158','?')
        return jsonify({'success':actual==target,'suction':actual})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}),500

@app.route('/find', methods=['POST'])
def find():
    try:
        d = dev()
        d.set_value(159, False)
        time.sleep(1)
        d.set_value(159, True)
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}),500

if __name__ == '__main__':
    logger.info(f'TinyTuya Vacuum on :{API_PORT} -> {DEVICE_IP}')
    app.run(host='0.0.0.0', port=API_PORT, debug=False)