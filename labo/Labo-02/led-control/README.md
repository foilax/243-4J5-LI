# ProjetMisession - Jeu de Voiture sur LilyGo ESP32

## Projet

Jeu de dodge cars (évitement de voitures) exécuté sur un LilyGo ESP32 avec écran TFT, contrôlé via:
- 3 potentiomètres pour configurer le jeu
- 2 boutons pour déplacer le joueur
- 2 LEDs status
- Écran tactile Raspberry Pi pour l'affichage déporté

## Matériel Requis

### Carte ESP32 LilyGo
- Affichage TFT 240x135
- 3 potentiomètres (pins 32, 34, 35)
- 2 boutons (pins 36, 39) avec pull-up externe
- 2 LEDs (pins 14, 25)

### Raspberry Pi
- Écran tactile 240x135
- Connexion série USB au LilyGo

### Câblage

| Composant | Pin ESP32 | Note |
|-----------|-----------|------|
| Potentiomètre voies | GPIO 32 | |
| Potentiomètre vitesse | GPIO 34 | |
| Potentiomètre taux | GPIO 35 | |
| Bouton gauche | GPIO 36 | Avec pull-up externe |
| Bouton droite | GPIO 39 | Avec pull-up externe |
| LED1 | GPIO 25 | |
| LED2 | GPIO 14 | |

## Installation

### 1. Prérequis Système

```bash
# Raspberry Pi
sudo apt-get update
sudo apt-get install python3-pygame python3-serial

# Arduino IDE
# Installer TFT_eSPI et WiFi library
```

### 2. Installation des Dépendances Python

```bash
cd /home/foilax/243-4J5-LI/labo/Labo-02/led-control
pip3 install -r requirements.txt
```

### 3. Upload Arduino

1. Ouvrir `lilygo_inputs/lilygo_inputs.ino` dans Arduino IDE
2. Sélectionner carte "ESP32 Dev Module"
3. Uploader sur le LilyGo

### 4. Connexion Série

Le LilyGo communique à 115200 baud. Assurez-vous que:
- Le cable USB est connecté
- Le port série est `/dev/ttyACM0` (ou adapter)

## Utilisation

### Lancer l'Interface sur Écran Tactile

```bash
# Via le script de lancement
cd /home/foilax/243-4J5-LI/labo/Labo-02/led-control
python3 touch_ui_simple.py
```

### Contrôles

#### Sur LilyGo (physiques)
- **Bouton GPIO 36**:_allervers la gauche
- **Bouton GPIO 39**:_allervers la droite

#### Sur Écran Tactile (Python UI)
- **LED1 ON/OFF**:_allumer/éteindre LED1
- **LED2 ON/OFF**:_allumer/éteindre LED2

#### Potentiomètres
| Potentiomètre | Pin | Fonction | Plage |
|---------------|-----|----------|-------|
| Voies | GPIO 32 | Nombre de voies | 2-8 |
| Vitesse | GPIO 34 | Vitesse du jeu | 50-200 km/h |
| Taux | GPIO 35 | Nb de voitures/seconde | 1-20/s |

## Structure des Fichiers

```
led-control/
├── touch_ui_simple.py       # Interface principale (curses)
├── touch_ui_mqtt.py         # Interface avec MQTT
├── dodge_game.py           # Jeu local (standalone)
├── monitor_serial.py       # Moniteur série
├── launch_on_screen.sh     # Script lancement écran
├── launch_game.sh          # Script lancement jeu
├── requirements.txt        # Dépendances Python
└── README.md               # Ce fichier

lilygo_inputs/
└── lilygo_inputs.ino       # Sketch Arduino principal
```

## Dépannage

### Pas de connexion série
```bash
# Vérifier les ports disponibles
ls -l /dev/ttyACM*
dmesg | grep tty
```

### Erreur "Permission denied" sur port série
```bash
# Ajouter l'utilisateur au groupe dialout
sudo usermod -a -G dialout $USER
# Puis se déconnecter et reconnecter
```

### Écran noir après lancement
- Vérifier que l'écran est branché et allumé
- Essayer Ctrl+Alt+F7 pour revenir au bureau

### Pas de réponse des boutons
- Vérifier le câblage
- Vérifier les pull-up externes (10kΩ vers VCC)

### LEDs ne clignotent pas
- Les LEDs sont maintenant contrôlées via l'écran tactile
- Envoyer `LED1:ON` ou `LED1:OFF` via série

## Exemples d'Utilisation

### Test des Entrées

```bash
# Monitor série
python3 monitor_serial.py
```

### Modifier la Vitesse

Tourner le potentiomètre sur GPIO 34 entre 50 et 200 km/h.

### Changer le Nombre de Voies

Tourner le potentiomètre sur GPIO 32 entre 2 et 8 voies.

## Auteurs

Projet pédagogique - Cours 243-4J5-LI

## Licence

Usage éducatif