include 

// À REMPLACER par vos informations
const char* ssid = "foilax";
const char* password = "Teladmin1$";

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("Test de connexion WiFi...");

  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nConnecté au WiFi!");
    Serial.print("Adresse IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nÉchec de connexion WiFi");
  }
}

void loop() {
  // Rien ici pour l'instant
  delay(1000);
}
