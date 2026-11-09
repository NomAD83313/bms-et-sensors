#!/usr/bin/env python
#============================================================================#
# 2018-09-28    Additional libary is necessary
#               https://pythonhosted.org/bitstring
#============================================================================#
#   Includes
#============================================================================#
#Flask
from flask import *
import jinja2.exceptions
from werkzeug import secure_filename
#Others
import threading
from messkluppe_nrf24 import *
import csv
import os.path
import time
import datetime
from ctypes import * 
import sqlite3
import os
import shlex
#from bitstring import BitArray
import numpy as np
import pandas as pd
from scipy import stats

# Don't display request msg
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

#============================================================================#
#   lib_nrf24 Setup
#============================================================================#
import RPi.GPIO as GPIO
from lib_nrf24 import NRF24
import spidev
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
global pipes
global radio
pipes = [0xAB, 0xCD, 0xAB, 0xCD, 0x71]
radio = NRF24(GPIO, spidev.SpiDev())
radio.begin(0, 22)
radio.setPayloadSize(32)
radio.setChannel(111)
radio.setDataRate(NRF24.BR_1MBPS)
radio.setPALevel(NRF24.PA_HIGH)
radio.setAutoAck(True)
radio.enableDynamicPayloads()
radio.enableAckPayload()
radio.setCRCLength(NRF24.CRC_8)
radio.openReadingPipe(1,pipes)
radio.startListening()
#============================================================================#
#   Flask Routs
#============================================================================#
app = Flask(__name__)
app.config.from_object(__name__)
app.config['SECRET_KEY'] = 'Messkluppe'
#============================================================================#
#   Let you shutdown the Python Server
#   2019-11-29
#============================================================================#
def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()

@app.route('/shutdown')
def shutdown():
    change_clip_modus(0)
    shutdown_server()
    return 'Server shutting down...'

#============================================================================#
#   Change the Live Data View Calculation
#   2019-11-29
#============================================================================#
@app.route('/change_live_display/<value>')
def change_live_display(value):
    g_clip['live_calculation'] = value
    return " "

@app.route('/test')
def test():
    create_csv()
    return " "

@app.route('/')
def index():
    change_clip_modus(0)
    
    return render_template('index.html', c=get_settings())

    

@app.route('/<pagename>')
def admin(pagename):
    change_clip_modus(0)
    return render_template(pagename+'.html', c=get_settings())

@app.route('/<path:resource>')
def serveStaticResource(resource):
	return send_from_directory('static/', resource)


#@app.route('/start_logging/<sampleRate>/<loggingTime>')
#def start_logging(sampleRate, loggingTime):
#    change_clip_modus(20)
#    g_clip['logging'] = True
#    # added THH 16032022 to be able to change sample rate and logging time
#    g_clip['sampleRate'] = int(sampleRate)
#    g_clip['loggingTime'] = int(loggingTime)
#    return str(" ")	

@app.route('/start_logging/<sampleRate>/<loggingTime>')
def start_logging(sampleRate,loggingTime):
    print ('===== start logging () =====')

    #print ('SR:'+str(sampleRate)+' LT: '+str(loggingTime))

    sql1 = "UPDATE settings SET value='"+ sampleRate +"' WHERE name='"+"sampleRate"+"'"
    sql2 = "UPDATE settings SET value='"+ loggingTime +"' WHERE name='"+"loggingTime"+"'"
    connection("w", sql1)
    connection("w", sql2)
    g_config = get_settings()
    g_clip['config'] = g_config

    g_clip['sampleRate'] = int(sampleRate)
    g_clip['loggingTime'] = int(loggingTime)  
    
    change_clip_modus(20)
    g_clip['logging'] = True
    
    return str(" ")
	
@app.route('/stop_logging')
def stop_logging():
    print ('===== stop logging () =====')
    change_clip_modus(0)
    g_clip['logging'] = False
    radio.flush_rx()
    radio.flush_tx()

    return str(" ")

@app.route('/start_deepSleep')
def start_deepSleep():
    change_clip_modus(10)
    g_clip['deepSleep'] = True
    return str(" ")	
	
@app.route('/stop_deepSleep')
def stop_deepSleep():
    g_clip['deepSleep'] = False
    change_clip_modus(0)
    radio.flush_rx()
    radio.flush_tx()

    return str(" ")	

@app.route('/get_globals')
def get_globals():
    g_clip['dl_file_len'] =  len(g_com_clip_file)
    g_clip['raspi_time'] = time.strftime('%Y-%m-%d | %H:%M:%S')
    return jsonify(g_clip)
        
@app.route('/file_download/<name>/<lines>')
def file_download(name, lines):
    g_clip['dl_finished'] = False
    g_clip['dl_started'] = False
    g_com_clip_file.clear()
    g_clip['dl_filename'] = int(name)
    g_clip['dl_from'] = 1
    g_clip['dl_until'] = int(lines)
    g_clip['dl_lines'] = int(lines)
    

    print("lines: "+ str(int(lines))) 

    change_clip_modus(40)
    return str(" ")	


@app.route('/file_delete/<name>')
def file_delete(name):
    g_clip['del_filename'] = int(name)
    change_clip_modus(50)
    return jsonify(result=True)
    
@app.route('/file_delete_all')
def file_delete_all():
    change_clip_modus(51)
    return jsonify(result=True)
    
@app.route('/live_data')
def live_data():    
       change_clip_modus(60)
       return render_template('live_data_dy.html', c=get_settings())

#@app.route('/live_data_temperature')
#def live_data_temperature():    
#       change_clip_modus(60)
#       return render_template('live_data_dy.html', c=get_settings())

#============================================================================#
#   Adds a time sync function from local client
#   2019-11-29
#============================================================================#
@app.route('/syncTime/<timestamp>')
def syncTime(timestamp): 
       timestamp = int(timestamp)/1000
       time = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
 
       import subprocess
       subprocess.call(["sudo", "date", "-s "+time])
       return render_template('index.html')
       
@app.route('/reset_send_clip_modus')
def reset_send_clip_modus():
    change_clip_modus(0)
    return ("n")

@app.errorhandler(jinja2.exceptions.TemplateNotFound)
def template_not_found(e):
    return not_found(e)

@app.errorhandler(404)
def not_found(e):
    return '<strong>Page Not Found!</strong>', 404
#============================================================================#
#   Displays all csv files stored on the raspberry
#   2018-08-10
#============================================================================#
@app.route('/local_files')
def local_files():
    change_clip_modus(0)  
    files = []
    sql = "SELECT * FROM local_files"
    files = connection("r", sql)
    
    
    return render_template('m_local_files.html', files=files, c=get_settings())
#============================================================================#
#   Displays one csv as graph
#   2018-08-10
#   2018-11-28 Changed Code to display the data from database
#   2020-02-05 Delete MDX and add "validation"
#   2020-02-19 Changed to Pandas DF
#============================================================================#
@app.route('/local_file_time/<id>')
def local_file(id, data=False):
    change_clip_modus(0)
    sql = "SELECT * FROM local_files WHERE id="+str(id)
    file = connection("r", sql)
    
    #file_name = file[0][2]
    #Read the CSV
    csv_data = pd.read_csv(g_path+file[0][2], header=None, delimiter=';', usecols=[4,5,6,7,8,9,10,11,12,13,16])
    #Name the columns
    csv_data.columns = ['ms', 'Fx', 'Fy', 'Fz','Ax', 'Ay', 'Yaw', 'Ti', 'Te', 'Vbat', 'timestamp' ]
    #Generate JS timestamp
    csv_data['timestamp'] = csv_data['timestamp']*1000+csv_data['ms']
   
    if (data == True):
        return (csv_data, file)
    else:
        return render_template('m_local_file_time.html', file=file[0][2], data=csv_data.to_dict(orient='records'))

#============================================================================#
#   Displays one csv as graph
#   2020-02-13 Search and delete bad lines
#   2020-02-19 Use local_file_time istead of get data again
#============================================================================#
@app.route('/local_file_filtered/<id>')
def local_file_filtered(id, data=False):
    change_clip_modus(0)
    csv_data, file = local_file(id, True)
    
    #Delete the Outlines
    csv_data = csv_data[np.logical_and(np.abs(stats.zscore(csv_data.Fx))<2, np.abs(stats.zscore(csv_data.Fy))<2, np.abs(stats.zscore(csv_data.Fz))<2)]
    #Smooth Curve
    csv_data['Fx'] = csv_data['Fx'].ewm(5).mean().round(1)
    csv_data['Fy'] = csv_data['Fy'].ewm(5).mean().round(1)
    csv_data['Fz'] = csv_data['Fz'].ewm(5).mean().round(1)
    if (data == True):
        return (csv_data, file)
    else:
        return render_template('m_local_file_time.html', file=file[0][2], data=csv_data.to_dict(orient='records'))

#============================================================================#
#   Displays one csv as graph with MDX data
#   2020-02-17 init
#============================================================================#
@app.route('/local_file_mdx/<id>')
def local_file_mdx(id):
    change_clip_modus(0)
    #Get csv Data from filtered data
    csv_data, file = local_file(id, True)
    csv_data['delta-t'] = abs(csv_data['timestamp'] - csv_data['timestamp'].shift(-1))
    csv_data['tabs'] = csv_data['delta-t'].cumsum()/1000
    
    #Get MDX
    mdx_json = mdx_show(file[0][1]).get_data(as_text=True)
    mdx = pd.read_json(mdx_json)
    mdx.columns = ['id', 'mdx_id', 'pos', 'mdx']
    mdx.drop("id", axis=1, inplace=True)
    mdx.drop("mdx_id", axis=1, inplace=True)
       
    #Add one line
    mdx.loc[-1] = [g_config['L_ClipTo0']/1000, 1]        # adding a row
    mdx.index = mdx.index + 1                            # shifting index
    mdx = mdx.sort_index()                               # sorting by index
    #Add clip speed
    mdx['v'] = file[0][5] / 60 * mdx['mdx']              #m/s
    #csv_data.at[0, 'v'] = file[0][5] / 60
    csv_data['v'] = file[0][5] / 60

    #Add acceleration
    # a = (next_v²-v²)/(2*(next_pos-pos))
    mdx['a'] = (mdx['v'].shift(-1)**2-mdx['v']**2)/(2*(mdx['pos'].shift(-1)-mdx['pos']))
        # geradlinige Bewegung t = delta(s)/v
    mdx.loc[mdx['a'] == 0, 't'] = (mdx['pos'].shift(-1) - mdx['pos'])/mdx['v']
        # beschleunigte Bewegung t = delta(v) / a
    mdx.loc[mdx['a'] != 0, 't'] = (mdx['v'].shift(-1) - mdx['v'])/mdx['a']

    #Start and End time
    mdx['t-end'] = mdx['t'].cumsum()
    mdx['t-start'] = mdx['t-end'] - mdx['t']
 
    #Select a from mdx

    for index, row in mdx.iterrows():
        #print(row)
        start = row['t-start']
        end = row ['t-end']
        a = row ['a']
        csv_data.loc[np.logical_and(csv_data['tabs']>start, csv_data['tabs']<end), "a"] = a
       

        #print(np.logical_and csv_data['tabs']>start)
        #print ( csv_data.loc[(csv_data['tabs']>start)&(csv_data['tabs']<end), 'a'])
        #csv_data.loc[(csv_data['tabs']>start)&(csv_data['tabs']<end), 'a'] = a

    csv_data['v'] = (csv_data['a']*csv_data['delta-t']/1000).cumsum()
    csv_data['v'] = csv_data['v'] + file[0][5] / 60
    csv_data['s'] = (((csv_data['v'].shift(-1)*(csv_data['delta-t']/1000) + 0.5*csv_data['a']*(csv_data['delta-t']/1000)**2)).cumsum()+(g_config['L_ClipTo0']/1000))
    #csv_data['x'] = (csv_data['v'].shift(-1)*(csv_data['delta-t']/1000) + 0.5*csv_data['a']*(csv_data['delta-t']/1000)**2)
    csv_data = csv_data.dropna()

    #print(csv_data)
    #print(mdx)

    #csv_data.to_csv(g_path+'silvo_csv.csv')

    return render_template('m_local_file_way.html', file=file[0][2], data=csv_data.to_dict(orient='records'), mdx=mdx.to_dict(orient='records'))

#============================================================================#
#   Delete a CSV file
#   2018-09-27
#   2018-11-28 Delete from Database and file
#============================================================================#
@app.route('/local_file_delete/<id>')
def local_file_delete(id):
    sql = "SELECT name FROM local_files WHERE id ="+str(id)
    filename = connection("r", sql)
    print (str(filename[0][0]))
    sql = "DELETE FROM local_files WHERE id ="+str(id)
    print (sql)
    connection("w", sql)

    if os.path.exists(g_path + str(filename[0][0])):
       os.remove(g_path + str(filename[0][0]))
       g_clip['local_files_len'] = len(next(os.walk(g_path))[2])
       return jsonify(result=True, name=filename[0][0])
    else:
      print("The file does not exist")
      return jsonify(result=True, name=filename[0][0])
#============================================================================#
#   Displays all files which are on the Arduino
#   2018-08-10
#   2018-09-10  filenames is in global so render the template and append the 
#               files after reciving all
#============================================================================#
@app.route('/online_files')
def online_files():
    g_clip['dl_started'] = False 
    g_clip['dl_finished'] = False 
    g_clip['dl_successfull'] = False 
    g_clip['online_files'].clear()

    change_clip_modus(30)

     
    return render_template('m_online_files.html', c=get_settings())
#============================================================================#
#   Settings
#   2018-11-23 Add Settings page
#============================================================================#	
@app.route('/settings')
def settings():
    change_clip_modus(0)
    return render_template('settings.html', c=get_settings())

#============================================================================#
#   Settings Save
#   2020-01-31 Update G_config to new saved Settings
#============================================================================#	
@app.route('/settings/save', methods=['POST'])
def setting_save():
    print(request.form)
    for r in request.form:
        sql = "UPDATE settings SET value='"+request.form[r]+"' WHERE name='"+r+"'"
        connection("w", sql)
    g_config = get_settings()
    g_clip['config'] = g_config  
    return (settings())
    
def get_settings():
    print ('===== get_settings() =====')
    sql = "SELECT * FROM settings"
    settings = connection("r", sql)
    config={}
    for s in settings:
        config[s[1]] = s[2]
    return config
  
def connection (task, sql): 
    try:
        connection = sqlite3.connect(os.path.dirname(os.path.abspath(__file__))+'/db.db')
        cursor = connection.cursor()
        cursor.execute(sql)
        
        
    except sqlite3.Error as e:
        print ("Database error: %s" % e)
    except Exception as e:
        print("Exception in _query: %s" % e)
 
    if task == "r":
        rows = cursor.fetchall()
        cursor.close()
        return rows
    else:
        id = cursor.lastrowid
        connection.commit()
        cursor.close()
        return id
        
#============================================================================#
#   Upload MDX File
#   2018-11-27 init
#============================================================================#
@app.route('/mdx_upload', methods = ['GET', 'POST'])
def mdx_upload():
    if request.method == 'POST':
        mdx_file = request.files['file'].filename
        f = request.files['file'].read()
        lines = f.decode("utf-8").splitlines()
        mdx_name = str(lines[0])

        del lines[0]
 
        mdx_dat = []
        for line in lines:
            line = ' '.join(line.split())                                       # removes all whitespaces if more then one
            if line.find('#'):
                line = line[0:line.find('#')]
            if line[0] != '#' and line[0] != 's':
                line = line.split(" ")
               
                mdx_dat.append([line[0].replace(',', '.'), line[1].replace(',', '.')])

    # Store the MDX in the database
    ## create MDX
    sql = "INSERT INTO mdx (name, filename) VALUES('"+mdx_name+"', '"+mdx_file+"')"
    id = connection("w", sql)
    ## Insert MDX Data
    for line in mdx_dat:
        sql = "INSERT INTO mdx_dat(mdx_id, pos, value) VALUES('"+str(id)+"', '"+str(line[0])+"', '"+str(line[1])+"')"
        connection("w", sql)
    return 'file uploaded successfully'

@app.route('/mdx_select')  
def mdx_select():
    sql = "SELECT * FROM mdx"
    mdx = connection("r", sql)
    
    return jsonify(mdx)

@app.route('/mdx_selected/<id>')  
def mdx_selected(id):
    sql = "SELECT mdx_id, inletSpeed, remark FROM local_files WHERE id = "+str(id)
    mdx_id = connection("r", sql)
        
    return jsonify(mdx_id)

@app.route('/mdx_save/<id>/<mdx>/<speed>/<remark>')  
def mdx_save(id, mdx, speed, remark):
    sql = "UPDATE local_files SET mdx_id = "+str(mdx)+", inletSpeed = "+str(speed)+", remark = "+str(remark)+" WHERE id = "+str(id)
    connection("w", sql)
    
    return jsonify(True)
#============================================================================#
#   mdx_show
#   2020-02-17  init
#               read one mdx file    
#============================================================================#
@app.route('/mdx_show/<id>')
def mdx_show(id):
    sql = "SELECT * FROM mdx_dat WHERE mdx_id = "+str(id)
    mdx = connection("r", sql)

    return jsonify(mdx)

#============================================================================#
#   Change the global variable to new mode
#============================================================================#
def change_clip_modus(new):
    if not g_clip['deepSleep']:
        print("change_clip_modus: " + str(g_clip['mode_pi']) + " --> " + str(new))
        g_clip['mode_pi'] = new	                                                 
        g_clip['new_task'] = True
    else:
        print("NO CHANGE! change_clip_modus: " + str(g_clip['mode_pi']) + " --> " + str(new))
#============================================================================#
#   Recalculate to Whatever
#   2019-12-09 init
#   2020-01-27 Extend to have all values
#
#   to = target, what value you want to have
#   cconfig = config
#   rawDate['Fx', 'Fy', 'Fz', 'Ya', 'Ti', 'Te', 'Vb']
#============================================================================#   
def raw_to_display (rawData, to, config):
    
    dispData = {}
    V_supply=3300
    Bit_max=4096
    VperBit=V_supply/Bit_max

    if (to == "raw"):
        dispData=rawData
        
        return (dispData)
    
    # Calculate the linear force

    dispData['F0'] = round((rawData['F0']-config['F0_b'])*config['F0_a']*VperBit,1)
    dispData['F1'] = round((rawData['F1']-config['F1_b'])*config['F1_a']*VperBit,1)
    dispData['F2'] = round((rawData['F2']-config['F2_b'])*config['F2_a']*VperBit,1)
    dispData['F3'] = round((rawData['F3']-config['F3_b'])*config['F3_a']*VperBit,1)
    dispData['F4'] = round((rawData['F4']-config['F4_b'])*config['F4_a']*VperBit,1)
    dispData['F5'] = round((rawData['F5']-config['F5_b'])*config['F5_a']*VperBit,1)
    dispData['F6'] = round((rawData['F6']-config['F6_b'])*config['F6_a']*VperBit,1)
    dispData['F7'] = round((rawData['F7']-config['F7_b'])*config['F7_a']*VperBit,1)
    
    dispData['Yaw'] = round(rawData['Yaw']/10,2)
    dispData['H1'] = rawData['H1']
    dispData['H2'] = rawData['H2']
    dispData['Ti'] = round(rawData['Ti']/100,1)
    dispData['Vb'] = round(((rawData['Vb']*config['B_V']/4096)*2)+config['B_V0'],2)


    #dispData['Fx'] = round(config['Fx_a']*rawData['Fx']+config['Fx_b'],1)
    #dispData['Fy'] = round(config['Fy_a']*rawData['Fy']+config['Fy_b'],1)
    #dispData['Fz'] = round(config['Fz_a']*rawData['Fz']+config['Fz_b'],1)
    #dispData['Ya'] = round(rawData['Ya']/10,2)
    #dispData['Ti'] = round(rawData['Ti']/100,1)
    #dispData['Te'] = round((rawData['Te']*config['TE_V']/4096+config['TE_V0'])*100,1)
    #dispData['Vb'] = round(((rawData['Vb']*config['B_V']/4096)*2)+config['B_V0'],2)
    #dispData['Ax'] = round(rawData['Ax']/1000,2)
    #dispData['Ay'] = round(rawData['Ay']/1000,2)
    
    if (to == "linearForce"):
        return (dispData)
    
    if (to == "compensatedForce"):
        dispData['Fx'] = round((dispData['Fx'] * config["Fx_Bfx"] + dispData['Fy'] * config["Fx_Bfy"] + dispData['Fz'] * config["Fx_Bfz"]) - config["Fx_B_Offset"],1)
        dispData['Fy'] = round((dispData['Fx'] * config["Fy_Bfx"] + dispData['Fy'] * config["Fy_Bfy"] + dispData['Fz'] * config["Fy_Bfz"]) - config["Fy_B_Offset"],1)
        dispData['Fz'] = round((dispData['Fx'] * config["Fz_Bfx"] + dispData['Fy'] * config["Fz_Bfy"] + dispData['Fz'] * config["Fz_Bfz"]) - config["Fz_B_Offset"],1)  
        return (dispData)
    return ('')
	

#============================================================================#
#============================================================================#
#   Create CSV
#   2020-01-27 Use 'raw_to_display' function
#   2020-02-04 Add filter function
#============================================================================#
def create_csv(name, data):
    for x in range(len(data)):

        #date = ((int(data[x][2]) << 16) + int(data[x][1]))
        time = data[x][2]
        #raw = {
        #    'Fx' : c_int32(data[x][]).value,
        #    'Fy' : c_int16(data[x][6]).value,
        #    'Fz' : c_int16(data[x][7]).value,
        #    'Ax' : c_int16(data[x][8]).value,
        #   'Ay' : c_int16(data[x][9]).value,
        #    'Ya': c_int16(data[x][10]).value,
        #    'Ti': data[x][11],
        #    'Te': data[x][12],
        #    'Vb': data[x][13]
        #}
        
        
        #new = raw_to_display(raw, 'linearForce', g_config)
        #data[x][5]  = new['Fx']
        #data[x][6]  = new['Fy']
       # data[x][7]  = new['Fz']
        #data[x][8]  = new['Ax']
        #data[x][9]  = new['Ay']
        #data[x][10] = new['Ya']
        #data[x][11] = new['Ti']
        #data[x][12] = new['Te']
       # data[x][13] = new['Vb']
        #data[x].append(date)


    name = str(name)+'.csv'    
    print ("========== Create CSV ==========")
    with open(g_path+name, mode='w') as file:
    
        csv_writer = csv.writer(file, delimiter=';')
        csv_writer.writerows(data)
    print ("========== Add to Database ==========")
    sql = "INSERT INTO local_files (numberLines, name) VALUES ('"+str(len(data))+"', '"+name+"')" 
    connection("w", sql)

#============================================================================#          
#============================================================================#
def com_clip ():  
    receivedMessage = [0, 0, 0, 0, 0, 0, 0, 0,] 
    RcvMsg = []
    # RcvMsg2 with 
    RcvMsg_file_dl = []
    RcvMsg_list = []
    #RcvMsg = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    SndMsg=[0, 0, 0, 0, 0, 0, 0, 0]
    last_timestamp = 0
    timeout = lastMsg = time.time()
    tfr = False
    send_answer = 0
    raw = {}


    while 1 :
        #---------- Live Data ----------------------------#   
        while (g_clip['mode_pi'] == 60):
            if radio.available() and radio.getDynamicPayloadSize() == 32:
                print ('##### Start Live Data #####')
                SndMsg = translate_to_radio ([1060, g_clip['com_timestamp']], False)
                radio.writeAckPayload(1, SndMsg, len(SndMsg))
                radio.read(receivedMessage, 32)
                RcvMsg = translate_from_radio(receivedMessage, 32, False)
                
                raw = {
                    'F0' : c_int16(RcvMsg[3]).value,
                    'F1' : c_int16(RcvMsg[4]).value,
                    'F2' : c_int16(RcvMsg[5]).value,
                    'F3' : c_int16(RcvMsg[6]).value,
                    'F4' : c_int16(RcvMsg[7]).value,
                    'F5' : c_int16(RcvMsg[8]).value,
                    'F6' : c_int16(RcvMsg[9]).value,
                    'F7' : c_int16(RcvMsg[10]).value,
                    'Yaw' : c_int16(RcvMsg[11]).value,
                    'H1' : c_int16(RcvMsg[12]).value,
                    'H2' : c_int16(RcvMsg[13]).value,
                    'Vb' : c_int16(RcvMsg[14]).value,
                    'Ti' : c_int16(RcvMsg[15]).value

                }
                
                g_config = get_settings()
                
                dispData = raw_to_display(raw, g_clip['live_calculation'], g_config)

                g_clip['live_F0'] = dispData['F0']
                g_clip['live_F1'] = dispData['F1']
                g_clip['live_F2'] = dispData['F2']
                g_clip['live_F3'] = dispData['F3']
                g_clip['live_F4'] = dispData['F4']
                g_clip['live_F5'] = dispData['F5']
                g_clip['live_F6'] = dispData['F6']
                g_clip['live_F7'] = dispData['F7']
                g_clip['live_YawAngle'] = dispData['Yaw']
                g_clip['live_Hall1'] = dispData['H1']
                g_clip['live_Hall2'] = dispData['H2']
                g_clip['live_Vbat'] = dispData['Vb']
                g_clip['live_Ti'] = dispData['Ti']

        if radio.available() and radio.getDynamicPayloadSize() == 32:
            #print ("========== Have Radio ==========")
            #-------------------------------------------------------------------------------#
            #-- Send a new Task to the Arduino if we have a stable connection and a new   --#
            #-- task else sent only the last RcvMsg                                       --#
            #-------------------------------------------------------------------------------#
            #print ("good connection = " + str(g_clip['good_connection']) + " new task = " + str(g_clip['new_task']))
            if (g_clip['good_connection'] == True and g_clip['new_task'] == True):
                #---------- Start deepSleep ---------------------------------#                
                if g_clip['mode_pi'] == 10:
                    print ('##### Start deepSleep #####')
                    SndMsg = translate_to_radio ([1010, g_clip['com_timestamp'], int(time.time())])
                    radio.writeAckPayload(1, SndMsg, len(SndMsg))
                #---------- Start Logging ---------------------------------#                
                if g_clip['mode_pi'] == 20:
                    print ('##### Start Logging #####')
                    SndMsg = translate_to_radio ([1020, g_clip['com_timestamp'], int(time.time()), g_clip['sampleRate'], g_clip['loggingTime']])
                    radio.writeAckPayload(1, SndMsg, len(SndMsg))
                #  Stop logging automatically after 1 second assuming that logging started within this time period
                    #time.sleep(1/100)
                    #stop_logging()

                #---------- Start online_files ----------------------------#                
                if g_clip['mode_pi'] == 30:
                    print ('##### Start File List #####')
                    SndMsg = translate_to_radio ([1030,g_clip['com_timestamp'] ,0 ,0 ,0 ,0 ,0 ,0], True)
                    radio.writeAckPayload(1, SndMsg, len(SndMsg))

                    time.sleep(1/100)
                    tfr = True
                #---------- Start file_download ---------------------------#                
                if g_clip['mode_pi'] == 40:
                    print ('##### Start File Download #####')
                    #----- dl_until must be int32
                    #  -> dl_until_pt1 = dl_until >> 16 / dl_until_pt2 = dl_until & xffff
                    SndMsg = translate_to_radio ([1040, g_clip['com_timestamp'], 0, g_clip['dl_filename'], g_clip['dl_from'], int(g_clip['dl_until'])>>16, int(g_clip['dl_until']) % 65536], True)
                    radio.writeAckPayload(1, SndMsg, len(SndMsg))
                    g_clip['dl_progress'] = 0

                    time.sleep(1/100)
                    tfr = True
                    
                #---------- Start file_delete -----------------------------#                
                if g_clip['mode_pi'] == 50:
                    SndMsg = translate_to_radio ([1050, g_clip['com_timestamp'], 0, g_clip['del_filename']], True)
                    radio.writeAckPayload(1, SndMsg, len(SndMsg))
                    
                 #---------- Start file_delete -----------------------------#                
                if g_clip['mode_pi'] == 51:
                    SndMsg = translate_to_radio ([1051, g_clip['com_timestamp'], 0], True)
                    radio.writeAckPayload(1, SndMsg, len(SndMsg))
  
                #---------- Acknoledge finished ---------------------------#                
                if g_clip['mode_pi'] == 99:
                    print ('##### Start Acknoledge #####')
                    SndMsg = translate_to_radio ([1099, g_clip['com_timestamp']], False)
                    radio.writeAckPayload(1, SndMsg, len(SndMsg))
                    change_clip_modus(0)
                    #tfr = False

                    
                # Task answer is prepared
                if g_clip['mode_pi'] == 20 or g_clip['mode_pi'] == 60:
                    print("20 or 60")
                else:
                    g_clip['new_task'] = False
            elif (g_clip['mode_pi'] == 0):
                SndMsg = translate_to_radio ([1000, g_clip['com_timestamp']], False)
                radio.writeAckPayload (1, SndMsg, len(SndMsg))

            
            # Read the msg and set gbl
            radio.read(receivedMessage, 32)
            RcvMsg  = translate_from_radio(receivedMessage, 32, False)
            #RcvMsg_list = translate_from_radio(receivedMessage, 32, False)
            RcvMsg_file_dl = translate_from_radio_file_dl(receivedMessage, 32, False)
            

            g_clip['RcvMsg'] = RcvMsg
            rcv_idTask = idTask(RcvMsg[0])
            g_clip['rcv_id'] = rcv_idTask[0]
            g_clip['rcv_task'] = rcv_idTask[1]
            
            # Check if Arduino is doing something
            #---------- File List (collect) ---------------------------#
            if (g_clip['rcv_task'] == 30):
                print ('##### File List (collect) #####')

                if (RcvMsg[1]!=0):                                     # Don't save the first line because its 0
                    g_clip['online_files'].append(RcvMsg)
                g_clip['dl_started'] = True
                g_clip['dontAnswer'] = True
                g_clip['dl_finished'] = False
                g_clip['dl_successfull'] = False
                continue
                
            #---------- File List (finished) --------------------------#
            elif (g_clip['rcv_task'] == 39 and g_clip['dl_finished'] == False):
                print ('##### File List (finished) #####')
                g_clip['dl_finished']= True
                g_clip['dl_successfull']= True
                g_clip['dontAnswer'] = False
                tfr = False
                radio.flush_rx
                radio.flush_tx
                change_clip_modus(99)
                
            #---------- File (collect) --------------------------------#  
            elif (g_clip['rcv_task'] == 40):    
                #print ('##### File (collect) #####')
                if (RcvMsg_file_dl[1]!=0):                                     # Don't save the first line because its 0 
                    g_com_clip_file.append(RcvMsg_file_dl)
                

                g_clip['dl_started'] = True
                g_clip['dontAnswer'] = True               
                g_clip['dl_progress'] = round((len(g_com_clip_file) / g_clip['dl_lines'])*100,1)
                #print('dl_progress :'+str(g_clip['dl_progress']))
                continue                
                
            #---------- File (finished) --------------------------------#
            #---------- 2020-01-24  changed to create the CSV even if  -#
            #----------             some lines are missing             -#
            #---------- 2020-01-27  Bugfix name must be a string!      -#
            elif (g_clip['rcv_task'] == 49 and g_clip['dl_finished'] == False):
                print ('##### File (finished) #####')
                g_clip['dl_finished']= True
                g_clip['dontAnswer'] = False
                change_clip_modus(99)

                if (g_clip['dl_file_len'] >= g_clip['dl_lines']):
                    create_csv(g_clip['dl_filename'], g_com_clip_file)
                else:
                    lines = g_clip['dl_lines'] - g_clip['dl_file_len']
                    filename = str(g_clip['dl_filename'])+"_missing_"+str(lines)+"_lines"
                    create_csv(filename, g_com_clip_file)
                    
                    g_com_clip_file.clear()
                    #g_clip['dl_dile_len'] = len(g_com_clip_file)
                    
            #---------- File (deleted) --------------------------------#
            elif (g_clip['rcv_task'] == 59):
                print ('##### File (deleted) #####')
                change_clip_modus(99)
            
            #---------- Calculate "good connection"----------------------#
            if (g_clip['rcv_task'] != 60):
                #print ('##### Calculate "good conection" #####')
                g_clip['com_timestamp'] = (RcvMsg[1] << 16) + RcvMsg[2]
                g_clip['com_ping'] = RcvMsg[3]
                g_clip['com_success'] = RcvMsg[4]
                g_clip['online_files_len'] = RcvMsg[5]                
                                
                #if (g_clip['com_ping'] <= 200 and  g_clip['com_success'] > 90):
                if (g_clip['com_success'] > 90):
                    g_clip['good_connection'] = True
                    lastMsg = time.time() 
                else:
                    g_clip['good_connection'] = False
           
           #---------- Dont Answer --------------------------------------#
            if (g_clip['dontAnswer'] == False):
                if (send_answer > 0):
                
                    print ('##### Answer #####' + str(send_answer))
                    send_answer -= 1
                    #print (SndMsg)

        elif (radio.getDynamicPayloadSize() != 32):
            #---------- Radio crashed! ---------------------------------#
            #print (" #---------- Radio crashed! - Wrong Payload Size ---------------------------------#" + str(radio.getDynamicPayloadSize()))
            radio.stopListening()
            radio.startListening()
            g_clip['com_ping'] = -1
            g_clip['good_connection'] = False
            
        else:
            time.sleep(1/1000)
            #print ("----------  NO  Radio ----------")
            g_clip['com_noMsg'] = round(time.time()-lastMsg, 1)
            if g_clip['com_noMsg'] > 5:
                g_clip['good_connection'] = False
                g_clip['com_ping'] = -1
                g_clip['com_success'] = 0
                g_clip['dontAnswer'] = False

#============================================================================#
#   globals
#   2020-01-31  Create global settings to reduce SQL queries
#
#   2022-03-16  added values: sampleRate and loggingTime
#============================================================================#

global g_com_clip_files
g_com_clip_files = []
global g_com_clip_file
g_com_clip_file = []
global g_path
g_path = "/home/pi/Flask_Messkluppe/static/_csv/"

global g_clip
g_clip = {}
g_clip['mode_pi'] = 0                       #
g_clip['dl_started'] = False                #
g_clip['dl_finished'] = False               #
g_clip['dl_successfull'] = False            #
g_clip['dl_filename'] = 0                   #
g_clip['dl_lines'] = 0                      #
g_clip['local_files_len'] = 0

g_clip['online_files'] = []
g_clip['new_task'] = False                  #
g_clip['good_connection'] = False           #
g_clip['com_ping'] = 0                      #
g_clip['com_success'] = 0                   #
g_clip['dontAnswer'] = False                #
g_clip['com_timestamp'] = 10
g_clip['live_calculation'] = 'linearForce'
g_clip['logging'] = False                   #
g_clip['deepSleep'] = False                 #

g_clip['sampleRate'] = 2500
g_clip['loggingTime'] = 20

g_clip['com_noMsg'] = 0                     #

global g_config
g_config = get_settings()
g_clip['config'] = g_config




 
@app.before_first_request
def activate_job():		
    t = threading.Thread(target=com_clip)
    t.start()
    
    # Read local files
    onlyfiles = next(os.walk(g_path))[2]
    g_clip['local_files_len'] = (len(onlyfiles))
   
   
#============================================================================#
#   start Flask Server
#============================================================================# 		
if __name__ == '__main__':    
    app.run(host='0.0.0.0', port=5000, debug=True)
    #app.run(host='10.3.141.1', port=5000, debug=True)
#============================================================================# 	


    