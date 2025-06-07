from machine import Pin
import bluetooth
import time
from time import sleep
import sys
import rp2
import network
import ubinascii
import machine
import socket
import onewire, ds18x20
import ntptime
import urequests
import select

# ### Constante ###
DEBUG = False
VERSION = "1.0a"

# UUIDs BLE UART
SERVICE_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
RX_UUID = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
TX_UUID = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

global gl_mode, gl_mode_old, gl_cde_regul, gl_reception_trame, gl_temp_pre_chauff, gl_temp_chauff, gl_ordre_pre_chauff, gl_ordre_chauff, gl_presence, gl_mode_debug, gl_current_hour, gl_current_minute, gl_defaut, gl_dem_chauffage_old

# Initialisation de la liste des températures
temperature_history = [20] # init une donnée

def mean(values):
    return sum(values) / len(values)


#############################################################
# ### /!\ !!! /!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!
# ### /!\ !!! lorsque l'on connecte la sonde usb,
# ### /!\ !!! ca finaera par crasher après la déconnecion
# ### /!\ !!! /!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!
############h#################################################
def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except Exception as e:
        pass  # Ignore les erreurs de sortie

global modetx
modetx="dummy"
#############################################################
# ### Fonction pour envoyer les donnees à SdB ###
############h#################################################
def send_to_SdB(temp, temp_cible, relais_state, mode, elapsed_time_regul_seconds, defaut, dem_chauffage):
    global modetx

    if mode=="off":
        # verif si la demande a ete refusee
        if dem_chauffage==True:
            # faire un chgmnt sur valeur de mode pour faire basculer les ihm de on/boost à off
            if modetx=="off_2":
                modetx="off_0"
            else:
                modetx="off_2" 
        else:
            modetx="off_0"              
    else:
        if relais_state==1:
            modetx=mode+"_1"
        else:
            modetx=mode+"_0" 
    # Donnees à envoyer
    #cde_regul_court=0 if cde_regul==False else 1
    data = f"{temp:.1f},{temp_cible:.1f},{modetx},{int(defaut)},{int(elapsed_time_regul_seconds/60)}"
    # defaut en valeur int (et non hex) :
    # il faut passer par un mode off->pre_chauff ou chauff pour effacer gl_defaut
        # bit 0: elapsed time regul
        # bit 1: timeout rx
        # bit 2:
        # bit 3:
        # bit 4:
        # bit 5:
        # bit 6:
        # bit 7:    
            
    safe_print("📥 Message transmis:", data)
    ble_server.send(data)
    
def to_bool(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1")
    if isinstance(val, int):
        return val == 1
    return False 

        
#############################################################
# ### Fonction pour analyser et affecter les valeurs des paramètres HTTP ###
#############################################################
def decode_rx_msg(message):
    global gl_reception_trame, gl_temp_pre_chauff, gl_temp_chauff, gl_mode_debug, gl_ordre_pre_chauff, gl_ordre_chauff, gl_presence, gl_current_hour, gl_current_minute

    try:
        # Décomposer la string pour obtenir les valeurs
        valeurs = message.split(',')
        if len(valeurs) != 8:
            safe_print("⚠️ Message mal formé:", message)
            return
        # Assigner les valeurs aux variables
        gl_presence = to_bool(valeurs[0])
        gl_temp_pre_chauff = float(16.0+int(valeurs[1])/2)
        gl_temp_chauff = float(16.0+int(valeurs[2])/2)
        gl_mode_debug = to_bool(valeurs[3])
        gl_ordre_pre_chauff = to_bool(valeurs[4])
        gl_ordre_chauff = to_bool(valeurs[5])
        gl_current_hour = int(valeurs[6])
        gl_current_minute = int(valeurs[7])

        gl_reception_trame=True
    except Exception as e:
        safe_print("⚠️ Erreur dans decode_rx_msg:", e)

#############################################################
# ### BLE Serveur ###
#############################################################  
class BLEServer:
    def __init__(self, name="PicoBLE"):
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self.on_event)
        
        self.last_msg_time = time.time()  # Initialisation

        self.conn_handle = None
        self.connected = False

        self.tx = (TX_UUID, bluetooth.FLAG_NOTIFY)
        self.rx = (RX_UUID, bluetooth.FLAG_WRITE)

        self.service = (SERVICE_UUID, (self.tx, self.rx))
        ((self.tx_handle, self.rx_handle),) = self.ble.gatts_register_services((self.service,))

        self.advertise(name)

    # def on_event(self, event, data):
    #     if event == 1:  # _IRQ_CENTRAL_CONNECT
    #         self.conn_handle, _, _ = data
    #         self.connected = True
    #         self.last_msg_time = time.time()  # Reset du timer
    #         safe_print("✅ Connecte")

    #     elif event == 2:  # _IRQ_CENTRAL_DISCONNECT
    #         safe_print("❌ Deconnecte")
    #         self.connected = False
    #         self.conn_handle = None
    #         self.advertise()
    def on_event(self, event, data):
        if event == 1:  # _IRQ_CENTRAL_CONNECT
            self.conn_handle, _, _ = data
            self.connected = True
            self.last_msg_time = time.time()  # Reset du timer
            safe_print("✅ Connecte")

        # elif event == 2:  # _IRQ_CENTRAL_DISCONNECT
        #     conn_handle, reason, mem_view = data
        #     safe_print(f"❌ Déconnecté - raison: conn_handle={conn_handle}, reason={reason}, memoryview={mem_view.tobytes()}")
        #     self.connected = False
        #     self.conn_handle = None
        #     self.ble.gap_advertise(None)  # Désactive l'advertising
        #     time.sleep(1)  # Délai avant de relancer la publicité
        #     self.advertise()          

        elif event == 2:  # _IRQ_CENTRAL_DISCONNECT
            conn_handle, addr_type, addr = data
            addr_str = ':'.join('{:02X}'.format(b) for b in bytes(addr))
            safe_print("❌ Déconnecté - handle={}, type={}, addr={}".format(conn_handle, addr_type, addr_str))
            self.connected = False
            self.conn_handle = None
            self.ble.gap_advertise(None)  # Stop advertising temporairement
            time.sleep(1)
           # self.ble.active(False)
           # time.sleep(1)
           # self.ble.active(True)
            self.advertise()


        elif event == 3:  # _IRQ RECEPTION (IRQ_GATTS_WRITE)          
            conn, attr = data
            if attr == self.rx_handle:
                msg_recu = self.ble.gatts_read(self.rx_handle).decode().strip()
                safe_print("📥 Message recu:", msg_recu)

                led_verte.value(1)
                # Analyser et mettre à jour les paramètres via la requete HTTP
                decode_rx_msg(msg_recu)

                self.last_msg_time = time.time()  # Mise à jour du timer

    def send(self, msg):
        if self.connected:
            try:
                self.ble.gatts_notify(self.conn_handle, self.tx_handle, msg)
            except Exception as e:
                safe_print("⚠️ Erreur BLE notify:", e)
                self.connected = False
                self.conn_handle = None
                self.advertise()

    def advertise(self, name="PicoBLE"):
        global gl_mode, gl_cde_regul
        
        name_bytes = bytes(name, 'utf-8')
        adv_data = bytearray(b'\x02\x01\x06')  # Flags
        adv_data += bytearray((len(name_bytes) + 1, 0x09)) + name_bytes  # Complete Local Name
        self.ble.gap_advertise(250000, adv_data)  # Pub plus lente, 1 seconde
        safe_print("📡 En attente de connexion BLE...")
        self.last_msg_time = time.time()
        # perte de comm => on coupe tout
        gl_mode="off"
        gl_cde_regul=False



    def check_timeout(self, timeout_s=1440): #time out en seconde. 1440s=24min
        global gl_defaut

        if self.connected:
            elapsed = time.time() - self.last_msg_time
            if elapsed > timeout_s:
                safe_print("⏱️ Aucun message depuis {} secondes. Deconnexion...".format(timeout_s))
                try:
                    self.ble.gap_disconnect(self.conn_handle)
                except:
                    safe_print("Erreur lors de la deconnexion forcée.")
                self.connected = False
                self.conn_handle = None
                self.advertise()
                #gl_defaut|=0x02

####################################################################################################

# ### Démarrage ###
safe_print("#########################")
safe_print(f"Version: {VERSION}")
safe_print("#########################")
pin = Pin("LED", Pin.OUT)

# ### Init Variables ###
gl_mode="off" 
gl_mode_old=gl_mode
gl_temp_pre_chauff=18
gl_temp_chauff=19
temp_cible=18
gl_presence=False
gl_ordre_pre_chauff=False
gl_ordre_chauff=False
gl_cde_regul=False
start_regul_ticks = 0
gl_mode_debug=False
gl_current_hour = 0
gl_current_minute = 0
#msg_recu = "null"
gl_reception_trame = False
gl_defaut = 0
gl_dem_chauffage_old=False

# ### Configuration du capteur de temperature ###
ds_pin = machine.Pin(0)  # GP0
ds_sensor = ds18x20.DS18X20(onewire.OneWire(ds_pin))
roms = ds_sensor.scan()
safe_print('Capteur DS18X20 detecte')

# ### Configuration des broches ###
relais = machine.Pin(1, machine.Pin.OUT)  # GP1 pour commande relais
led_verte = machine.Pin(26, machine.Pin.OUT)
led_verte.value(1)

# ### Démarrage du serveur BLE ###
ble_server = BLEServer()
safe_print(bluetooth.__name__)
led_verte.value(0)

last_temp_time = time.ticks_ms()
#############################################################
# ### Boucle principale ###
#############################################################
try:
    while True:
        ble_server.check_timeout(1440) # timer en secndes. pas de réception trame depuis plus de > xx minutes
        #time.sleep(10)
        
        # # ### Lecture du capteur de temperature ###
        # try:
        #     ds_sensor.convert_temp()
        #     time.sleep_ms(1250)
        #     temp = round(ds_sensor.read_temp(roms[0]), 1) if roms else None
        # except Exception as e:
        #     safe_print("❌ Erreur lecture capteur de température :", e)
        #     temp = 50 # pour forcer coupure relais

        # ### Lecture du capteur de temperature ###
        # try:
        #     ds_sensor.convert_temp()
        #     time.sleep_ms(1250)
        #     mesure = round(ds_sensor.read_temp(roms[0]), 1) if roms else 50
        # except Exception as e:
        #     safe_print("❌ Erreur lecture capteur de température :", e)
        #     mesure = 50 # pour forcer coupure relais

        now = time.ticks_ms()
        
        #if time.ticks_diff(now, last_temp_time) > 1250:
        try:
            ds_sensor.convert_temp()
            mesure = round(ds_sensor.read_temp(roms[0]), 1) if roms else 50
        except Exception as e:
            safe_print("❌ Erreur lecture capteur:", e)
            mesure = 50
        last_temp_time = now

        # Mise à jour de l'historique des températures
        if (mesure>11) or (mesure<35):
            temperature_history.append(mesure)
            if len(temperature_history) > 10:
                temperature_history.pop(0)  # Garder uniquement les x dernières valeurs



        temp = mean(temperature_history)

        #########################        
        # ### Regulation ###
        #########################
        if gl_cde_regul==True:
            elapsed_time_regul_seconds = (time.ticks_ms() - start_regul_ticks) // 1000
            if (elapsed_time_regul_seconds > max_timer_regul_seconds):
                gl_mode="off"
                relais.value(0)
                gl_cde_regul=False
                gl_defaut|=0x01
                safe_print(f"{gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")
            else:
                if temp <= (temp_cible-0.5):
                    relais.value(1)
                    time.sleep_ms(1000)
                elif temp >= (temp_cible+0.5):
                    relais.value(0)
        else:
            relais.value(0)
            elapsed_time_regul_seconds=0
        

        ###########################        
        # ### chgmnt de mode ? ###
        ###########################
        tx_trame=False
        if gl_reception_trame:
            gl_reception_trame=False
            tx_trame=True
            led_verte.value(0)
            # safe_print(f"gl_presence:{gl_presence}")
            # safe_print(f"gl_ordre_chauff:{gl_ordre_chauff}")
            
            dem_chauffage=(gl_ordre_pre_chauff or gl_ordre_chauff)
            if (dem_chauffage) and (gl_dem_chauffage_old==False):
                gl_defaut=0
            gl_dem_chauffage_old=dem_chauffage
            
            if(gl_presence==True)and(gl_defaut==0):
                if (gl_ordre_pre_chauff==True) and (gl_mode!="pre_chauff") and (gl_current_hour >= 20) and (gl_temp_pre_chauff<=19):
                    temp_cible=gl_temp_pre_chauff
                    gl_mode="pre_chauff"
                    gl_cde_regul=True
                    start_regul_ticks = time.ticks_ms()
                    max_timer_regul_seconds=2*3600
                    if(temp<(gl_temp_pre_chauff+0.5)):
                        relais.value(1)
                    safe_print(f"{gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")
                elif (gl_ordre_chauff==True) and (gl_mode!="chauff") and (gl_temp_chauff<=20) : 
                    if( (gl_mode_debug==True) or (gl_current_hour >= 19) or (gl_current_hour <=3) ):# debug
                        temp_cible=gl_temp_chauff
                        gl_mode="chauff"
                        gl_cde_regul=True
                        start_regul_ticks = time.ticks_ms()
                        max_timer_regul_seconds=45*60
                        if(temp<(gl_temp_chauff+0.5)):
                            relais.value(1)
                        safe_print(f"{gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")
                elif (gl_ordre_pre_chauff==False) and (gl_ordre_chauff==False) and (gl_mode!="off") :
                    gl_mode="off"
                    relais.value(0)
                    gl_cde_regul=False
                    safe_print(f"{gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()} ")
                elif (gl_mode=="pre_chauff") and (temp_cible!=gl_temp_pre_chauff):
                    temp_cible=gl_temp_pre_chauff
                elif (gl_mode=="chauff") and (temp_cible!=gl_temp_chauff):
                    temp_cible=gl_temp_chauff
            elif(gl_mode!="off"):
                gl_mode="off"
                relais.value(0)
                gl_cde_regul=False
                safe_print(f"Passage mode off (pres off ou gl_defaut) : {gl_mode} {gl_defaut} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")

        #########################        
        # ### Emission trame ###
        #########################
        if tx_trame or gl_mode_old!=gl_mode:
            # if gl_reception_trame:
            #     led_verte.value(1)
            #     # Analyser et mettre à jour les paramètres via la requete HTTP
            #     decode_rx_msg(msg_recu)
            #     led_verte.value(0)
            if gl_mode_old!=gl_mode:
                gl_mode_old = gl_mode
            send_to_SdB(temp, temp_cible, relais.value(), gl_mode, elapsed_time_regul_seconds, gl_defaut, dem_chauffage)

except Exception as e:
    safe_print("❌ Exception dans la boucle principale:", e)
   #machine.reset()  # Redémarre le Pico automatiquement








 
