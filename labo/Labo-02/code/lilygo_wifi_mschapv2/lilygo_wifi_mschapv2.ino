// LilyGO WiFi - Version avec MQTT via WebSocket SSL
// Utilise PubSubClient avec wrapper WebSocket pour simplifier le code MQTT

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <esp_wpa2.h> // Keep for Enterprise WiFi
#include <mbedtls/base64.h>

#include "auth.h" // Fichier contenant les identifiants WiFi et MQTT

// ====== CONFIG MQTT/WSS ======
const char* MQTT_HOST = MQTT_BROKER;         // Broker depuis auth.h
const int   MQTT_WSS_PORT = 443;             // Port sécurisé SSL
const char* MQTT_PATH = "/";                 // WebSocket path

char BUTTON1_STATE_TOPIC[50];
char BUTTON2_STATE_TOPIC[50];
char LED1_SET_TOPIC[50];
char LED2_SET_TOPIC[50];

// --- Configuration des broches (Pins) ---
const int LED1_PIN = 32;
const int LED2_PIN = 33;
const int BUTTON1_PIN = 34;
const int BUTTON2_PIN = 35;

// ============================================================================
// CLASSE WRAPPER WEBSOCKET POUR PUBSUBCLIENT
// ============================================================================

class WebSocketClient : public Client {
private:
  WiFiClientSecure* _sslClient;
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
  WebSocketClient(WiFiClientSecure* sslClient) {
    _sslClient = sslClient;
    _wsConnected = false;
    _rxBufferLen = 0;
    _rxBufferPos = 0;
  }

  int connect(IPAddress ip, uint16_t port) { return 0; }
  int connect(const char *host, uint16_t port) {
    //Serial.println("[WSS] Connexion SSL...");

    if (!_sslClient->connect(host, port)) {
      Serial.println("[WSS] Echec connexion SSL");
      return 0;
    }

    //Serial.println("[WSS] SSL connecte, envoi handshake WebSocket...");
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

WiFiClientSecure wifiClient;
WebSocketClient wsClient(&wifiClient);
PubSubClient mqttClient(wsClient);

// État
long lastButtonCheck = 0;
int lastButton1State = HIGH;
int lastButton2State = HIGH;

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
      digitalWrite(LED1_PIN, HIGH);
      Serial.println("[LED1] Allumee (ROUGE)");
    } else if (msg == "OFF") {
      digitalWrite(LED1_PIN, LOW);
      Serial.println("[LED1] Eteinte");
    }
  }
  else if (strcmp(topic, LED2_SET_TOPIC) == 0) {
    if (msg == "ON") {
      digitalWrite(LED2_PIN, HIGH);
      Serial.println("[LED2] Allumee (VERTE)");
    } else if (msg == "OFF") {
      digitalWrite(LED2_PIN, LOW);
      Serial.println("[LED2] Eteinte");
    }
  }
}

// ============================================================================
// FONCTIONS
// ============================================================================

void checkButtons() {
  long now = millis();

  if (now - lastButtonCheck < 100) {
    return;
  }
  lastButtonCheck = now;

  if (!mqttClient.connected()) return;

  int button1State = digitalRead(BUTTON1_PIN);
  if (button1State != lastButton1State) {
    lastButton1State = button1State;
    const char* state = (button1State == LOW) ? "PRESSED" : "RELEASED";
    mqttClient.publish(BUTTON1_STATE_TOPIC, state);
    Serial.print("[BTN1] -> ");
    Serial.println(state);
  }

  int button2State = digitalRead(BUTTON2_PIN);
  if (button2State != lastButton2State) {
    lastButton2State = button2State;
    const char* state = (button2State == LOW) ? "PRESSED" : "RELEASED";
    mqttClient.publish(BUTTON2_STATE_TOPIC, state);
    Serial.print("[BTN2] -> ");
    Serial.println(state);
  }
}

bool reconnectMQTT() {
  //Serial.println("[MQTT] Connexion au broker...");

  if (mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
    Serial.println("[MQTT] Connecte!");

    mqttClient.subscribe(LED1_SET_TOPIC);
    mqttClient.subscribe(LED2_SET_TOPIC);
    //Serial.println("[MQTT] Souscriptions envoyees");

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
  Serial.println("=== LilyGo WiFi - MQTT via WebSocket SSL ===");
  Serial.println();

  pinMode(LED1_PIN, OUTPUT);
  pinMode(LED2_PIN, OUTPUT);
  pinMode(BUTTON1_PIN, INPUT_PULLUP);
  pinMode(BUTTON2_PIN, INPUT_PULLUP);

  digitalWrite(LED1_PIN, LOW);
  digitalWrite(LED2_PIN, LOW);

  Serial.print("Connexion WiFi a ");
  Serial.println(WIFI_SSID);

  WiFi.disconnect(true);
  WiFi.mode(WIFI_STA);

#ifdef WIFI_SECURITY_WPA2_ENTERPRISE
  //Serial.println("Using WPA2-Enterprise connection.");
  esp_wifi_sta_wpa2_ent_set_identity((uint8_t *)EAP_IDENTITY, strlen(EAP_IDENTITY));
  esp_wifi_sta_wpa2_ent_set_username((uint8_t *)EAP_USERNAME, strlen(EAP_USERNAME));
  esp_wifi_sta_wpa2_ent_set_password((uint8_t *)EAP_PASSWORD, strlen(EAP_PASSWORD));
  esp_wifi_sta_wpa2_ent_enable();
  WiFi.begin(WIFI_SSID);
#elif defined(WIFI_SECURITY_WPA2_PERSONAL)
  //Serial.println("Using WPA2-Personal connection.");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
#else
  //Serial.println("Using Open/Undefined WiFi connection.");
  WiFi.begin(WIFI_SSID);
#endif

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connecte!");
  Serial.print("Adresse IP: ");
  Serial.println(WiFi.localIP());

  // Topics MQTT
  snprintf(LED1_SET_TOPIC, sizeof(LED1_SET_TOPIC), "%s/led/1/set", MQTT_CLIENT_ID);
  snprintf(LED2_SET_TOPIC, sizeof(LED2_SET_TOPIC), "%s/led/2/set", MQTT_CLIENT_ID);
  snprintf(BUTTON1_STATE_TOPIC, sizeof(BUTTON1_STATE_TOPIC), "%s/button/1/state", MQTT_CLIENT_ID);
  snprintf(BUTTON2_STATE_TOPIC, sizeof(BUTTON2_STATE_TOPIC), "%s/button/2/state", MQTT_CLIENT_ID);

  Serial.print("[MQTT] Device ID: ");
  Serial.println(MQTT_CLIENT_ID);

  // Configurer WiFiClientSecure
  //Serial.println("[SSL] Configuration du client SSL...");
  wifiClient.setInsecure(); // Désactiver la vérification des certificats pour la démo

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
  // Vérifier la connexion WiFi
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Connexion perdue, reconnexion...");
    while (WiFi.status() != WL_CONNECTED) {
      delay(500);
      Serial.print(".");
    }
    Serial.println("\nWiFi reconnecte!");

    if (wsClient.connect(MQTT_HOST, MQTT_WSS_PORT)) {
      reconnectMQTT();
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
