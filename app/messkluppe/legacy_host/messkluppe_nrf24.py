#============================================================================#
#============================================================================#
#   translate radio Msq from byte to int
#============================================================================#
def translate_from_radio(msg, size, debug=False):
    try:
        translated_msg=[]
        for i in range (0, size, 2):
            #translated_msg.append(int.from_bytes([msg[i+3], msg[i+2], msg[i+1], msg[i]], byteorder='big')) 
            translated_msg.append(int.from_bytes([ msg[i+1], msg[i]], byteorder='big')) 
            
        if (debug):
            #print("Translate FROM Radio: " + str(msg) + " --> " + str(translated_msg))
            print("Translate FROM Radio: " + str(translated_msg))
        return translated_msg
    except:
        print("----------> Bad msg from radio")
        return 0
#============================================================================#
#============================================================================#
#   Split the msg element in 4 bytes and add it to translated msg
#============================================================================#   
def translate_to_radio(msg, debug=False):
    try:
        translated_msg=[]
        for i in range (0, len(msg)):
            x=msg[i].to_bytes(4, byteorder='big')
            for g in reversed(x):
                translated_msg.append(g)        
        if (debug):    
            #print("Translate TO Radio: " + str(msg) + " --> " + str(translated_msg))
            print("Translate TO Radio:" + str(msg))
        return translated_msg
    except:
        print("----------> Bad msg to radio")
        print(msg)
        return []
#============================================================================#
#============================================================================#
#   Seperates the Clip ID and the Task | idTask[ID, Task] ID = 22 * 1000 + Task
#============================================================================#
def idTask (idTask):
    if type(idTask) is int:
        create_task = int(idTask%1000)
        create_id = int((idTask-create_task)/1000)
        new = [create_id, create_task]
        return new
		
    if type(idTask) is list:
        new = idTask[0] * 1000 + idTask[1]
        return new
#============================================================================#

#============================================================================#
#   translate radio Msq from byte to int
#   method for file download- line number and time are longint
#============================================================================#
def translate_from_radio_file_dl(msg, size, debug=False):
    try:
        translated_msg=[]
        translated_msg.append(int.from_bytes([ msg[1], msg[0]], byteorder='big'))
        #translated_msg.append(int.from_bytes(msg[3],msg[2],[msg[5], msg[4]], byteorder='big'))
        #translated_msg.append(int.from_bytes([msg[9], msg[8],msg[7],msg[6]], byteorder='big')) 

        translated_msg.append((int.from_bytes([msg[3],msg[2]], byteorder='big') << 16) + int.from_bytes([msg[5], msg[4]],byteorder='big'))
        translated_msg.append((int.from_bytes([msg[7],msg[6]], byteorder='big') << 16) + int.from_bytes([msg[9], msg[8]],byteorder='big'))
        for i in range (3, 14):
            #translated_msg.append(int.from_bytes([msg[i+3], msg[i+2], msg[i+1], msg[i]], byteorder='big')) 
            translated_msg.append(int.from_bytes([msg[2*i+5], msg[2*i+4]], byteorder='big'))    
            
        if (debug):
            #print("Translate FROM Radio: " + str(msg) + " --> " + str(translated_msg))
            print("Translate FROM Radio (dl): " + str(translated_msg))
        return translated_msg
    except:
        print("----------> Bad msg from radio")
        return 0       
