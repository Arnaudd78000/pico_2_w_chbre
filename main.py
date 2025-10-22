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
VERSION = "1.5"

# defaut  :
    # bit 1: elapsed time regul
    # bit 2: erreur decodage rx msg
    # bit 3: timeout réception -> reset
    # bit 4: defaut capteur temp
    # bit 5: passage en mode presence off pdt regul
    # bit 6: refus ordre (mauvaise heure ou temp_cible)
    # bit 7:  
    # bit 8:     

# UUIDs BLE UART
SERVICE_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
RX_UUID = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
TX_UUID = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")
RX2_UUID = bluetooth.UUID("6E400004-B5A3-F393-E0A9-E50E24DCCA9E")  # nouvel UUID pour trame refresh
TX_DEFAUT_UUID = bluetooth.UUID("6E400005-B5A3-F393-E0A9-E50E24DCCA9E")

global gl_mode, gl_mode_old, gl_cde_regul, gl_reception_trame, gl_temp_chauff, gl_ordre_on, gl_ordre_boost, gl_presence, gl_mode_debug, gl_current_hour, gl_current_minute, gl_defaut, gl_dem_chauffage_old, gl_duree, gl_ma_duree
gl_duree=0
gl_ma_duree=0

# Initialisation de la liste des températures
temperature_history = [20] # init une donnée

def mean(values):
    return sum(values) / len(values)


#############################################################
# ### /!\ !!! /!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!/!\ !!!
# ### /!\ !!! lorsque l'on connecte la sonde usb,
# ### /!\ !!! ca finaera par crasher après la déconnexion
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
def send_to_SdB(temp, temp_cible, relais_state, mode, elapsed_time_regul_seconds, duree, dem_chauffage):
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
    data = f"{temp:.1f},{temp_cible:.1f},{modetx},{duree},{int(elapsed_time_regul_seconds/60)}"
            
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
# ### Decode msg bluetooth from raspy SdB                 ###
#############################################################
def decode_rx_msg(message):
    global gl_reception_trame, gl_temp_chauff, gl_mode_debug, gl_ordre_on, gl_ordre_boost, gl_presence, gl_current_hour, gl_current_minute, gl_duree, gl_defaut

    try:
        # Décomposer la string pour obtenir les valeurs
        valeurs = message.split(',')
        if len(valeurs) != 8:
            safe_print("⚠️ Message rx1 mal formé:", message)
            gl_defaut|=0x02
            return
        # Assigner les valeurs aux variables
        gl_presence = to_bool(valeurs[0])
        gl_mode_debug = to_bool(valeurs[2])
        gl_ordre_on = to_bool(valeurs[3])
        gl_ordre_boost = to_bool(valeurs[4])
        if gl_ordre_on or gl_ordre_boost:
            gl_temp_chauff = float(int(valeurs[1]))/10
            gl_duree = int(valeurs[5])        
        gl_current_hour = int(valeurs[6])
        gl_current_minute = int(valeurs[7])

        safe_print(" Message rx :", message)
        gl_reception_trame=True
    except Exception as e:
        safe_print("⚠️ Erreur dans decode_rx_msg:", e)
        gl_defaut|=0x02

def decode_rx2_msg(message):
    global gl_reception_trame, gl_presence, gl_current_hour, gl_current_minute, gl_mode, gl_defaut

    try:
        # Décomposer la string pour obtenir les valeurs
        valeurs = message.split(',')
        if len(valeurs) != 4:
            safe_print("⚠️ Message rx2 mal formé:", message)
            gl_defaut|=0x02
            return
        # Assigner les valeurs aux variables
        gl_presence = to_bool(valeurs[0])
        mode = to_bool(valeurs[1])
        if ((mode==False) and (gl_mode!="off")) or (gl_presence==False):
            gl_mode="off"
            relais.value(0) 
        gl_current_hour = int(valeurs[2])
        gl_current_minute = int(valeurs[3])

        safe_print(" Message refresh:", message)
        gl_reception_trame=True
    except Exception as e:
        safe_print("⚠️ Erreur dans decode_rx2_msg:", e)
        gl_defaut|=0x02

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
        self.tx_defaut = (TX_DEFAUT_UUID, bluetooth.FLAG_NOTIFY)        
        self.rx = (RX_UUID, bluetooth.FLAG_WRITE)
        self.rx2 = (RX2_UUID, bluetooth.FLAG_WRITE)  # deuxième RX

        self.service = (SERVICE_UUID, (self.tx, self.tx_defaut, self.rx, self.rx2))
        ((self.tx_handle, self.tx_defaut_handle, self.rx_handle, self.rx2_handle),) = self.ble.gatts_register_services((self.service,))

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
                safe_print("📥 Message RX recu:", msg_recu)

                led_verte.value(1)
                # Analyser et mettre à jour les paramètres via la requete HTTP
                decode_rx_msg(msg_recu)

                self.last_msg_time = time.time()  # Mise à jour du timer

            elif attr == self.rx2_handle:
                msg = self.ble.gatts_read(self.rx2_handle).decode().strip()
                safe_print("📥 Message RX 2 reçu:", msg)
                decode_rx2_msg(msg)  # fonction distincte pour RX2 si tu veux

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
                
    def send_defaut(self):
        global gl_defaut  
        if self.connected:
            try:
                data = f"0x{gl_defaut:02X}"   # Affiche toujours 2 chiffres (ex : 0x03, 0xAF)
                safe_print("📥 Message transmis:", data)
                self.ble.gatts_notify(self.conn_handle, self.tx_defaut_handle, data)
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



    def check_timeout(self, timeout_disconnect=1440, timeout_reset=14400):
        global gl_defaut

        if self.connected:
            elapsed = time.time() - self.last_msg_time

            if elapsed > timeout_reset:
                safe_print("⏱️ Aucun message depuis {} secondes. Redémarrage du système...".format(timeout_reset))
                gl_defaut|=0x04
                ble_server.send_defaut()
                time.sleep(1)
                machine.reset()

            elif elapsed > timeout_disconnect:
                safe_print("⏱️ Aucun message depuis {} secondes. Deconnexion BLE...".format(timeout_disconnect))
                try:
                    self.ble.gap_disconnect(self.conn_handle)
                except:
                    safe_print("Erreur lors de la deconnexion forcée.")
                self.connected = False
                self.conn_handle = None
                self.advertise()

####################################################################################################

# ### Démarrage ###
safe_print("#########################")
safe_print(f"Version: {VERSION}")
safe_print("#########################")
pin = Pin("LED", Pin.OUT)

# ### Init Variables ###
gl_mode="off" 
gl_mode_old=gl_mode
gl_temp_chauff=19
temp_cible=18
gl_presence=False
gl_ordre_on=False
gl_ordre_boost=False
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
    erreur_capteur=False
    while True:
        ble_server.check_timeout(timeout_disconnect=1440, timeout_reset=14400)  # 24 min / 4 hours # timer en secndes. pas de réception trame depuis plus de > xx minutes
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
            erreur_capteur=True 
        last_temp_time = now

        # Mise à jour de l'historique des températures
        if (mesure>11) or (mesure<35):
            temperature_history.append(mesure)
            if len(temperature_history) > 10:
                temperature_history.pop(0)  # Garder uniquement les x dernières valeurs
                if erreur_capteur:
                  gl_defaut|=0x08
                  erreur_capteur=False



        temp = mean(temperature_history)

        #########################        
        # ### Regulation ###
        #########################
        if gl_cde_regul==True:
            elapsed_time_regul_seconds = (time.ticks_ms() - start_regul_ticks) // 1000
            if (elapsed_time_regul_seconds > max_timer_regul_seconds):
                gl_mode="off"
                gl_ordre_boost=False
                gl_ordre_on=False
                relais.value(0)
                gl_cde_regul=False

                safe_print(f"Fin: {gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")
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
            # safe_print(f"gl_ordre_boost:{gl_ordre_boost}")
            
            dem_chauffage=(gl_ordre_on or gl_ordre_boost)
            gl_dem_chauffage_old=dem_chauffage
            if (gl_defaut!=0) :
                ble_server.send_defaut()
                gl_defaut=0
                
            
            if(gl_presence==True):
                ################ PRE CHAUFFAGE ####################
                if (gl_ordre_on==True):
                  if (gl_current_hour >= 19) and (gl_temp_chauff<=19):
                    if (gl_mode!="pre_chauff"):
                        temp_cible=gl_temp_chauff
                        gl_mode="pre_chauff"
                        gl_cde_regul=True
                        start_regul_ticks = time.ticks_ms()
                        max_timer_regul_seconds=min(2*3600, (1+gl_duree)*3600)  # duree max de 2h
                        gl_ma_duree = gl_duree
                        if(temp<(gl_temp_chauff+0.5)):
                            relais.value(1)
                        safe_print(f"{gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")
                    elif gl_duree!=gl_ma_duree:
                        max_timer_regul_seconds=min(2*3600, (1+gl_duree )*3600)  # duree max de 2h
                        gl_ma_duree = gl_duree
                    elif (temp_cible!=gl_temp_chauff):
                        temp_cible=gl_temp_chauff
                  else:
                    gl_defaut|=0x20
                ################ CHAUFFAGE ####################                        
                elif (gl_ordre_boost==True):
                  if( (gl_temp_chauff<=20) and ( (gl_mode_debug==True) or (gl_current_hour >= 19) or (gl_current_hour <=3) )):
                        if (gl_mode!="chauff") :
                            temp_cible=gl_temp_chauff
                            gl_mode="chauff"
                            gl_cde_regul=True
                            start_regul_ticks = time.ticks_ms()
                            max_timer_regul_seconds=min(60*30, (1+gl_duree)*60*15)  # duree max de 30min
                            gl_ma_duree = gl_duree                        
                            if(temp<(gl_temp_chauff+0.5)):
                                relais.value(1)
                            safe_print(f"{gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")
                        elif gl_duree!=gl_ma_duree:
                            max_timer_regul_seconds=min(60*30, (1+gl_duree)*60*15)  # duree max de 30min
                            gl_ma_duree = gl_duree  
                        elif (temp_cible!=gl_temp_chauff):
                            temp_cible=gl_temp_chauff
                  else:
                    gl_defaut|=0x20                            
                ################ PASSAGE OFF ####################                                                     
                elif (gl_ordre_on==False) and (gl_ordre_boost==False) and (gl_mode!="off") :
                    gl_mode="off"
                    relais.value(0)
                    gl_cde_regul=False
                    safe_print(f"{gl_mode} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()} ")
            elif(gl_mode!="off"):
                gl_mode="off"
                relais.value(0)
                gl_defaut|=0x10                
                gl_cde_regul=False
                safe_print(f"Passage mode off (pres off ou gl_defaut) : {gl_mode} {gl_defaut} {temp} {temp_cible} {gl_current_hour}:{gl_current_minute} {relais.value()}")

        #########################        
        # ### Emission trame ###
        #########################
        if tx_trame or gl_mode_old!=gl_mode:
            if gl_mode_old!=gl_mode:
                gl_mode_old = gl_mode
            send_to_SdB(temp, temp_cible, relais.value(), gl_mode, elapsed_time_regul_seconds, gl_ma_duree, dem_chauffage)

except Exception as e:
    safe_print("❌ Exception dans la boucle principale:", e)
   #machine.reset()  # Redémarre le Pico automatiquement