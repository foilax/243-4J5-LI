// LilyGO T-SIM A7670G - Version LTE/Cellulaire avec MQTT via WebSocket SSL
// Utilise PubSubClient avec wrapper WebSocket pour simplifier le code MQTT

#define TINY_GSM_MODEM_SIM7600
#define TINY_GSM_RX_BUFFER 1024

#include <TinyGsmClient.h>
#include <PubSubClient.h>

// ESP_SSLClient configuration
#define ENABLE_DEBUG
#define ENABLE_ERROR_STRING
#define DEBUG_PORT Serial
#define SSLCLIENT_INSECURE_ONLY

#include <ESP_SSLClient.h>
#include <mbedtls/base64.h>

#include "auth.h"

// ====== CONFIG MODEM A7670G ======
#define MODEM_TX 26
#define MODEM_RX 27
#define MODEM_PWRKEY 4
#define MODEM_DTR 12
#define MODEM_RI 13
#define MODEM_FLIGHT 25
#define MODEM_STATUS 0

#define SD_MISO 2
#define SD_MOSI 15
#define SD_SCLK 14
#define SD_CS 13

// ====== CONFIG MQTT/WSS ======
const char* MQTT_HOST = MQTT_BROKER;
const int   MQTT_WSS_PORT = 443;
const char* MQTT_PATH = "/";

char BUTTON1_STATE_TOPIC[50];
char BUTTON2_STATE_TOPIC[50];
char LED1_SET_TOPIC[50];
char LED2_SET_TOPIC[50];
char LED1_STATE_TOPIC[50];
char LED2_STATE_TOPIC[50];

// --- Configuration des broches (Pins) ---
const int LED1_PIN = 32;
const int LED2_PIN = 33;
const int BUTTON1_PIN = 34;
const int BUTTON2_PIN = 35;

// Serial pour le modem
HardwareSerial SerialAT(1);

// ============================================================================
// CLASSE WRAPPER WEBSOCKET POUR PUBSUBCLIENT
// ============================================================================

class WebSocketClient : public Client {
private:
  ESP_SSLClient* _sslClient;
  bool _wsConnected;

  // Buffer pour les données reçues
  uint8_t _rxBuffer[512];
  size_t _rxBufferLen;
  size_t _rxBufferPos;

  String generateWebSocketKey() {
    uint8_t key[16];
    for(int i = 0; i < 16; i++) {
      key[i] = random(0, 256);
    }
    size_t olen;
    unsigned char output[64];
    mbedtls_base64_encode(output, sizeof(output), &olen, key, 16);
    return String((char*)output);
  }

  bool readWebSocketFrame() {
    if (!_sslClient->available()) return false;

    uint8_t byte1 = _sslClient->read();
    if (!_sslClient->available()) return false;
    uint8_t byte2 = _sslClient->read();

    uint8_t opcode = byte1 & 0x0F;
    bool masked = (byte2 & 0x80) != 0;
    size_t payloadLen = byte2 & 0x7F;

    if (payloadLen == 126) {
      if (_sslClient->available() < 2) return false;
      payloadLen = (_sslClient->read() << 8) | _sslClient->read();
    } else if (payloadLen == 127) {
      if (_sslClient->available() < 8) return false;
      payloadLen = 0;
      for(int i = 0; i < 8; i++) {
        payloadLen = (payloadLen << 8) | _sslClient->read();
      }
    }

    uint8_t mask[4] = {0};
    if (masked) {
      if (_sslClient->available() < 4) return false;
      for(int i = 0; i < 4; i++) {
        mask[i] = _sslClient->read();
      }
    }

    if (opcode == 0x01 || opcode == 0x02) { // Text ou Binary
      if (_sslClient->available() < payloadLen) return false;

      _rxBufferLen = payloadLen < sizeof(_rxBuffer) ? payloadLen : sizeof(_rxBuffer);
      for(size_t i = 0; i < _rxBufferLen; i++) {
        _rxBuffer[i] = _sslClient->read();
        if (masked) _rxBuffer[i] ^= mask[i % 4];
      }
      _rxBufferPos = 0;
      return true;
    }
    else if (opcode == 0x08) { // Close
      Serial.println("[WSS] Serveur a ferme la connexion");
      _wsConnected = false;
      return false;
    }
    else if (opcode == 0x09) { // Ping
      uint8_t pong[2] = {0x8A, 0x00};
      _sslClient->write(pong, 2);
      return false;
    }

    return false;
  }

public:
  WebSocketClient(ESP_SSLClient* sslClient) {
    _sslClient = sslClient;
    _wsConnected = false;
    _rxBufferLen = 0;
    _rxBufferPos = 0;
  }

  int connect(IPAddress ip, uint16_t port) { return 0; }
  int connect(const char *host, uint16_t port) {
    Serial.println("[WSS] Connexion SSL...");

    if (!_sslClient->connect(host, port)) {
      Serial.println("[WSS] Echec connexion SSL");
      return 0;
    }

    Serial.println("[WSS] SSL connecte, envoi handshake WebSocket...");
    String wsKey = generateWebSocketKey();

    _sslClient->print("GET ");
    _sslClient->print(MQTT_PATH);
    _sslClient->print(" HTTP/1.1\r\nHost: ");
    _sslClient->print(host);
    _sslClient->print("\r\nUpgrade: websocket\r\n");
    _sslClient->print("Connection: Upgrade\r\n");
    _sslClient->print("Sec-WebSocket-Key: ");
    _sslClient->print(wsKey);
    _sslClient->print("\r\nSec-WebSocket-Protocol: mqtt\r\n");
    _sslClient->print("Sec-WebSocket-Version: 13\r\n\r\n");

    unsigned long timeout = millis();
    while (!_sslClient->available() && millis() - timeout < 5000) {
      delay(10);
    }

    if (!_sslClient->available()) {
      Serial.println("[WSS] Timeout handshake");
      return 0;
    }

    String response = "";
    while (_sslClient->available()) {
      char c = _sslClient->read();
      response += c;
      if (response.endsWith("\r\n\r\n")) break;
    }

    if (response.indexOf("101") > 0 && response.indexOf("Switching Protocols") > 0) {
      Serial.println("[WSS] Handshake WebSocket reussi!");
      _wsConnected = true;
      return 1;
    } else {
      Serial.println("[WSS] Handshake WebSocket echoue");
      return 0;
    }
  }

  size_t write(uint8_t b) {
    return write(&b, 1);
  }

  size_t write(const uint8_t *buf, size_t size) {
    if (!_wsConnected) return 0;

    // Frame WebSocket binaire avec masque
    uint8_t header[14];
    int headerLen = 2;

    header[0] = 0x82; // FIN + Binary frame

    if (size < 126) {
      header[1] = 0x80 | size;
    } else if (size < 65536) {
      header[1] = 0x80 | 126;
      header[2] = (size >> 8) & 0xFF;
      header[3] = size & 0xFF;
      headerLen = 4;
    } else {
      header[1] = 0x80 | 127;
      for(int i = 0; i < 8; i++) header[2 + i] = 0;
      header[6] = (size >> 24) & 0xFF;
      header[7] = (size >> 16) & 0xFF;
      header[8] = (size >> 8) & 0xFF;
      header[9] = size & 0xFF;
      headerLen = 10;
    }

    // Masque aléatoire
    uint8_t mask[4];
    for(int i = 0; i < 4; i++) {
      mask[i] = random(0, 256);
      header[headerLen + i] = mask[i];
    }
    headerLen += 4;

    _sslClient->write(header, headerLen);

    for(size_t i = 0; i < size; i++) {
      uint8_t maskedByte = buf[i] ^ mask[i % 4];
      _sslClient->write(&maskedByte, 1);
    }

    return size;
  }

  int available() {
    // D'abord vérifier le buffer
    if (_rxBufferPos < _rxBufferLen) {
      return _rxBufferLen - _rxBufferPos;
    }

    // Sinon essayer de lire une nouvelle frame
    if (_sslClient->available()) {
      if (readWebSocketFrame()) {
        return _rxBufferLen - _rxBufferPos;
      }
    }

    return 0;
  }

  int read() {
    if (_rxBufferPos < _rxBufferLen) {
      return _rxBuffer[_rxBufferPos++];
    }

    if (_sslClient->available()) {
      if (readWebSocketFrame() && _rxBufferPos < _rxBufferLen) {
        return _rxBuffer[_rxBufferPos++];
      }
    }

    return -1;
  }

  int read(uint8_t *buf, size_t size) {
    size_t count = 0;
    while (count < size) {
      int c = read();
      if (c < 0) break;
      buf[count++] = (uint8_t)c;
    }
    return count;
  }

  int peek() {
    if (_rxBufferPos < _rxBufferLen) {
      return _rxBuffer[_rxBufferPos];
    }
    return -1;
  }

  void flush() {
    _sslClient->flush();
  }

  void stop() {
    _wsConnected = false;
    _sslClient->stop();
  }

  uint8_t connected() {
    return _wsConnected && _sslClient->connected();
  }

  operator bool() {
    return _wsConnected;
  }
};

// ============================================================================
// CLIENTS ET MQTT
// ============================================================================

TinyGsm modem(SerialAT);
TinyGsmClient gsmClient(modem, 0);
ESP_SSLClient sslClient;
WebSocketClient wsClient(&sslClient);
PubSubClient mqttClient(wsClient);

// État
long lastButtonCheck = 0;
int lastButton1State = HIGH;
int lastButton2State = HIGH;
unsigned long lastGprsCheck = 0;
const unsigned long GPRS_CHECK_INTERVAL = 30000;

// Variables globales pour debounce et toggle
bool led1State = false;
bool led2State = false;
unsigned long lastButton1Press = 0;
unsigned long lastButton2Press = 0;
const unsigned long DEBOUNCE_DELAY = 200;  // 200ms

// ============================================================================
// CALLBACK MQTT
// ============================================================================

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }

  Serial.print("[MQTT] <- ");
  Serial.print(topic);
  Serial.print(" = ");
  Serial.println(msg);

  if (strcmp(topic, LED1_SET_TOPIC) == 0) {
    if (msg == "ON") {
      led1State = true;
      digitalWrite(LED1_PIN, HIGH);
      Serial.println("[LED1] Allumee (ROUGE)");
    } else if (msg == "OFF") {
      led1State = false;
      digitalWrite(LED1_PIN, LOW);
      Serial.println("[LED1] Eteinte");
    }
  }
  else if (strcmp(topic, LED2_SET_TOPIC) == 0) {
    if (msg == "ON") {
      led2State = true;
      digitalWrite(LED2_PIN, HIGH);
      Serial.println("[LED2] Allumee (VERTE)");
    } else if (msg == "OFF") {
      led2State = false;
      digitalWrite(LED2_PIN, LOW);
      Serial.println("[LED2] Eteinte");
    }
  }
}

// ============================================================================
// FONCTIONS MODEM
// ============================================================================

void modemPowerOn() {
  Serial.println("[MODEM] Allumage du modem...");
  pinMode(MODEM_PWRKEY, OUTPUT);
  digitalWrite(MODEM_PWRKEY, HIGH);
  delay(100);
  digitalWrite(MODEM_PWRKEY, LOW);
  delay(1000);
  digitalWrite(MODEM_PWRKEY, HIGH);
  delay(3000);
  Serial.println("[MODEM] Modem allume");
}

bool initModem() {
  Serial.println("[MODEM] Initialisation...");

  SerialAT.begin(115200, SERIAL_8N1, MODEM_RX, MODEM_TX);
  delay(3000);

  if (!modem.restart()) {
    Serial.println("[MODEM] Echec du redemarrage");
    return false;
  }

  String modemInfo = modem.getModemInfo();
  Serial.print("[MODEM] Info: ");
  Serial.println(modemInfo);

  String imei = modem.getIMEI();
  Serial.print("[MODEM] IMEI: ");
  Serial.println(imei);

  Serial.print("[MQTT] Device ID: ");
  Serial.println(MQTT_CLIENT_ID);

  snprintf(LED1_SET_TOPIC, sizeof(LED1_SET_TOPIC), "%s/led/1/set", MQTT_CLIENT_ID);
  snprintf(LED2_SET_TOPIC, sizeof(LED2_SET_TOPIC), "%s/led/2/set", MQTT_CLIENT_ID);
  snprintf(BUTTON1_STATE_TOPIC, sizeof(BUTTON1_STATE_TOPIC), "%s/button/1/state", MQTT_CLIENT_ID);
  snprintf(BUTTON2_STATE_TOPIC, sizeof(BUTTON2_STATE_TOPIC), "%s/button/2/state", MQTT_CLIENT_ID);
  snprintf(LED1_STATE_TOPIC, sizeof(LED1_STATE_TOPIC), "%s/led/1/state", MQTT_CLIENT_ID);
  snprintf(LED2_STATE_TOPIC, sizeof(LED2_STATE_TOPIC), "%s/led/2/state", MQTT_CLIENT_ID);

  Serial.println("[MODEM] Initialise");
  return true;
}

bool connectToNetwork() {
  Serial.println("[NETWORK] Configuration de l'APN...");

  modem.sendAT("+CGDCONT=1,\"IP\",\"", APN, "\"");
  if (modem.waitResponse() != 1) {
    Serial.println("[NETWORK] Echec de configuration APN");
  } else {
    Serial.println("[NETWORK] APN configure");
  }

  Serial.println("[NETWORK] Connexion au reseau cellulaire...");

  if (!modem.waitForNetwork(60000L)) {
    Serial.println("[NETWORK] Echec de connexion au reseau");
    return false;
  }

  String operator_name = modem.getOperator();
  Serial.print("[NETWORK] Operateur: ");
  Serial.println(operator_name);

  int signalQuality = modem.getSignalQuality();
  Serial.print("[NETWORK] Signal: ");
  Serial.print(signalQuality);
  Serial.println(" dBm");

  Serial.println("[GPRS] Connexion GPRS...");
  if (!modem.gprsConnect(APN, APN_USER, APN_PASS)) {
    Serial.println("[GPRS] Echec de connexion GPRS");
    return false;
  }

  if (!modem.isGprsConnected()) {
    Serial.println("[GPRS] GPRS non connecte");
    return false;
  }

  IPAddress ip = modem.localIP();
  Serial.print("[GPRS] IP: ");
  Serial.println(ip);
  Serial.println("[GPRS] Connecte");

  return true;
}

void checkButtons() {
  unsigned long now = millis();

  if (!mqttClient.connected()) return;

  // Bouton 1 - Toggle LED 1
  if (digitalRead(BUTTON1_PIN) == LOW) {  // Bouton pressé (pull-up)
    if (now - lastButton1Press > DEBOUNCE_DELAY) {
      lastButton1Press = now;
      led1State = !led1State;  // Toggle
      digitalWrite(LED1_PIN, led1State ? HIGH : LOW);

      // Publier l'état sur MQTT
      const char* state = led1State ? "ON" : "OFF";
      mqttClient.publish(LED1_STATE_TOPIC, state);
      Serial.print("[BTN1] LED1 toggled: ");
      Serial.println(state);
      
      // On publie aussi l'état du bouton pour l'interface
      mqttClient.publish(BUTTON1_STATE_TOPIC, "PRESSED");
    }
  } else {
      if (now - lastButton1Press > DEBOUNCE_DELAY) {
          // Relaché (simplifié pour l'interface)
      }
  }

  // Bouton 2 - Toggle LED 2
  if (digitalRead(BUTTON2_PIN) == LOW) {  // Bouton pressé (pull-up)
    if (now - lastButton2Press > DEBOUNCE_DELAY) {
      lastButton2Press = now;
      led2State = !led2State;  // Toggle
      digitalWrite(LED2_PIN, led2State ? HIGH : LOW);

      // Publier l'état sur MQTT
      const char* state = led2State ? "ON" : "OFF";
      mqttClient.publish(LED2_STATE_TOPIC, state);
      Serial.print("[BTN2] LED2 toggled: ");
      Serial.println(state);
      
      // On publie aussi l'état du bouton pour l'interface
      mqttClient.publish(BUTTON2_STATE_TOPIC, "PRESSED");
    }
  }
}

bool reconnectMQTT() {
  Serial.println("[MQTT] Connexion au broker...");

  if (mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
    Serial.println("[MQTT] Connecte!");

    mqttClient.subscribe(LED1_SET_TOPIC);
    mqttClient.subscribe(LED2_SET_TOPIC);
    Serial.println("[MQTT] Souscriptions envoyees");

    return true;
  }

  Serial.print("[MQTT] Echec, code: ");
  Serial.println(mqttClient.state());
  return false;
}

// ============================================================================
// SETUP
// ============================================================================

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println();
  Serial.println("=== LilyGo T-SIM A7670G - MQTT via LTE + WebSocket SSL ===");
  Serial.println();

  pinMode(LED1_PIN, OUTPUT);
  pinMode(LED2_PIN, OUTPUT);
  pinMode(BUTTON1_PIN, INPUT_PULLUP);
  pinMode(BUTTON2_PIN, INPUT_PULLUP);

  digitalWrite(LED1_PIN, LOW);
  digitalWrite(LED2_PIN, LOW);

  modemPowerOn();

  if (!initModem()) {
    Serial.println("[ERREUR] Impossible d'initialiser le modem");
    while (true) {
      digitalWrite(LED1_PIN, !digitalRead(LED1_PIN));
      delay(200);
    }
  }

  if (!connectToNetwork()) {
    Serial.println("[ERREUR] Impossible de se connecter au reseau");
    while (true) {
      digitalWrite(LED1_PIN, !digitalRead(LED1_PIN));
      delay(500);
    }
  }

  // Configurer ESP_SSLClient
  Serial.println("[SSL] Configuration du client SSL...");
  sslClient.setClient(&gsmClient);
  sslClient.setInsecure();
  sslClient.setBufferSizes(8192, 2048);
  sslClient.setDebugLevel(1);

  // Configurer MQTT
  mqttClient.setServer(MQTT_HOST, MQTT_WSS_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setKeepAlive(60);

  // Connexion WebSocket et MQTT
  if (!wsClient.connect(MQTT_HOST, MQTT_WSS_PORT)) {
    Serial.println("[ERREUR] Impossible de se connecter via WebSocket");
    while (true) {
      digitalWrite(LED1_PIN, !digitalRead(LED1_PIN));
      delay(1000);
    }
  }

  if (!reconnectMQTT()) {
    Serial.println("[ERREUR] Impossible de se connecter au broker MQTT");
    while (true) {
      digitalWrite(LED1_PIN, !digitalRead(LED1_PIN));
      delay(1000);
    }
  }

  Serial.println();
  Serial.println("=== Systeme pret ===");
  Serial.println();

  for (int i = 0; i < 3; i++) {
    digitalWrite(LED1_PIN, HIGH);
    digitalWrite(LED2_PIN, HIGH);
    delay(200);
    digitalWrite(LED1_PIN, LOW);
    digitalWrite(LED2_PIN, LOW);
    delay(200);
  }
}

// ============================================================================
// LOOP
// ============================================================================

void loop() {
  unsigned long now = millis();

  // Vérifier la connexion GPRS
  if (now - lastGprsCheck > GPRS_CHECK_INTERVAL) {
    lastGprsCheck = now;

    if (!modem.isGprsConnected()) {
      Serial.println("[GPRS] Connexion perdue");

      if (connectToNetwork()) {
        if (wsClient.connect(MQTT_HOST, MQTT_WSS_PORT)) {
          reconnectMQTT();
        }
      }
    }
  }

  // Maintenir la connexion MQTT
  if (!mqttClient.connected()) {
    reconnectMQTT();
  }

  mqttClient.loop();

  // Vérifier les boutons
  checkButtons();

  delay(10);
}
