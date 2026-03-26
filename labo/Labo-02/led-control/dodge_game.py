#!/usr/bin/env python3
import curses
import time
import random
import threading
from queue import Queue

try:
    from gpiozero import Button
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("gpiozero non disponible - utilisation du clavier (a/d)")

try:
    from evdev import InputDevice, ecodes, list_devices
    TOUCH_AVAILABLE = True
except ImportError:
    TOUCH_AVAILABLE = False
    print("evdev non disponible - pas de support tactile")

try:
    import paho.mqtt.client as mqtt
    import ssl
    from mqtt_config import MQTT_CONFIG
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    print("MQTT non disponible")

# Configuration GPIO (numéros BCM pour gpiozero)
# Pin 36 (BOARD) = GPIO 16, Pin 39 (BOARD) = GPIO 19
PIN_LEFT = 16
PIN_RIGHT = 19

# Configuration du jeu
NUM_LANES = 3
PLAYER_CHAR = "▲"
CAR_CHAR = "▣"
ROAD_MARK = "│"
ROAD_EDGE = "║"
EMPTY = " "

class ButtonReader:
    def __init__(self, event_queue: Queue):
        self.event_queue = event_queue
        self.gpio_available = False

        if GPIO_AVAILABLE:
            try:
                # pull_up=True car bouton relié à GND avec pull-up externe
                self.btn_left = Button(PIN_LEFT, pull_up=True, bounce_time=0.15)
                self.btn_right = Button(PIN_RIGHT, pull_up=True, bounce_time=0.15)

                self.btn_left.when_pressed = self._left_pressed
                self.btn_right.when_pressed = self._right_pressed

                self.gpio_available = True
                print(f"[ButtonReader] GPIO initialisé: LEFT=GPIO{PIN_LEFT}, RIGHT=GPIO{PIN_RIGHT}")
            except Exception as e:
                print(f"[ButtonReader] Erreur GPIO: {e}")

    def _left_pressed(self):
        print("[ButtonReader] LEFT pressé!")
        self.event_queue.put("left")

    def _right_pressed(self):
        print("[ButtonReader] RIGHT pressé!")
        self.event_queue.put("right")

class MQTTReader:
    def __init__(self, event_queue: Queue):
        self.event_queue = event_queue
        self.client = None
        self.connected = False

        if MQTT_AVAILABLE:
            try:
                device_id = MQTT_CONFIG.get("device_id", "esp32-foilax")

                self.btn1_topic = f"{device_id}/button/1/state"
                self.btn2_topic = f"{device_id}/button/2/state"
                self.pot1_topic = f"{device_id}/pot/1/value"
                self.pot2_topic = f"{device_id}/pot/2/value"

                client_id = f"dodge-game-{int(time.time())}"
                self.client = mqtt.Client(client_id=client_id, transport="websockets")

                self.client.tls_set(ca_certs=None, cert_reqs=ssl.CERT_REQUIRED)
                self.client.username_pw_set(
                    MQTT_CONFIG.get("username", "esp_user"),
                    MQTT_CONFIG.get("password", "")
                )

                self.client.on_connect = self._on_connect
                self.client.on_message = self._on_message

                broker = MQTT_CONFIG.get("broker", "mqtt.foilax.org")
                port = MQTT_CONFIG.get("port", 443)

                print(f"[MQTTReader] Connexion à {broker}:{port}...")
                self.client.connect(broker, port, 60)
                self.client.loop_start()

            except Exception as e:
                print(f"[MQTTReader] Erreur: {e}")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            print("[MQTTReader] Connecté!")
            client.subscribe(self.btn1_topic)
            client.subscribe(self.btn2_topic)
            client.subscribe(self.pot1_topic)
            client.subscribe(self.pot2_topic)
        else:
            print(f"[MQTTReader] Erreur connexion: {rc}")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode('utf-8', errors='ignore').strip()

        if topic == self.btn1_topic and payload == "1":
            self.event_queue.put("left")
        elif topic == self.btn2_topic and payload == "1":
            self.event_queue.put("right")
        elif topic == self.pot1_topic:
            try:
                val = int(payload)
                self.event_queue.put(("speed", val))
            except ValueError:
                pass
        elif topic == self.pot2_topic:
            try:
                val = int(payload)
                self.event_queue.put(("lanes", val))
            except ValueError:
                pass

    def stop(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

class TouchReader(threading.Thread):
    def __init__(self, event_queue: Queue):
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.device = self._find_touch_device()
        if not self.device:
            print("[TouchReader] Aucun périphérique touchscreen trouvé.")
            return

        # Récupérer les infos d'axes pour calibrer
        abs_x = self.device.absinfo(ecodes.ABS_MT_POSITION_X)
        abs_y = self.device.absinfo(ecodes.ABS_MT_POSITION_Y)

        self.min_x, self.max_x = abs_x.min, abs_x.max
        self.min_y, self.max_y = abs_y.min, abs_y.max

        self.current_x = (self.min_x + self.max_x) // 2
        self.current_y = (self.min_y + self.max_y) // 2

    def _find_touch_device(self):
        """Trouve un device touchscreen"""
        if not TOUCH_AVAILABLE:
            return None
        for path in list_devices():
            dev = InputDevice(path)
            name = dev.name.lower()
            if "touch" in name or "ft5406" in name:
                print(f"[TouchReader] Using device: {dev.name} ({path})")
                return dev
        return None

    def run(self):
        if not self.device:
            return
        for event in self.device.read_loop():
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_MT_POSITION_X:
                    self.current_x = event.value
                elif event.code == ecodes.ABS_MT_POSITION_Y:
                    self.current_y = event.value

            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                if event.value == 1:  # touch down
                    self.event_queue.put(("touch", self.current_x, self.current_y))

class Car:
    def __init__(self, lane, row):
        self.lane = lane
        self.row = row
        self.width = 3

class DodgeGame:
    def __init__(self, stdscr, event_queue: Queue):
        self.stdscr = stdscr
        self.event_queue = event_queue
        self.running = True
        self.game_over = False
        self.paused = False

        # État du jeu
        self.player_lane = 1  # 0, 1, 2 (gauche, centre, droite)
        self.cars = []
        self.score = 0
        self.speed = 0.15  # Vitesse de descente des voitures
        self.spawn_rate = 0.03  # Probabilité de spawn par frame
        self.last_move = 0

        # Vitesse en km/h (50-150)
        self.speed_kmh = 80
        self.min_speed_kmh = 50
        self.max_speed_kmh = 150

        # Nombre de voies (2-8)
        self.num_lanes = 3
        self.min_lanes = 2
        self.max_lanes = 8

        # Dimensions de la route
        self.lane_width = 5   # Largeur d'une voie

        # Dimensions écran (initialisées dans run())
        self.height = 24
        self.width = 80

        # Zone du bouton restart (sera calculée dans _draw_game_over)
        self.restart_btn = {"row": 0, "col": 0, "height": 5, "width": 25}

        # Zones des boutons gauche/droite (seront calculées dans _draw_ui)
        self.left_btn = {"row": 0, "col": 0, "height": 5, "width": 10}
        self.right_btn = {"row": 0, "col": 0, "height": 5, "width": 10}

        # Zones des boutons vitesse +/-
        self.speed_up_btn = {"row": 0, "col": 0, "height": 3, "width": 5}
        self.speed_down_btn = {"row": 0, "col": 0, "height": 3, "width": 5}

        # Zones des boutons voies +/-
        self.lanes_up_btn = {"row": 0, "col": 0, "height": 3, "width": 5}
        self.lanes_down_btn = {"row": 0, "col": 0, "height": 3, "width": 5}

    @property
    def road_width(self):
        return self.num_lanes * self.lane_width

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)    # Joueur
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)      # Voitures
        curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)    # Route
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # Marquage route
        curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)     # Score
        curses.init_pair(6, curses.COLOR_RED, curses.COLOR_BLACK)      # Game over
        curses.init_pair(7, curses.COLOR_GREEN, curses.COLOR_BLACK)    # Succès
        curses.init_pair(8, curses.COLOR_BLUE, curses.COLOR_BLACK)     # Boutons tactiles
        curses.init_pair(9, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # Vitesse

    def _update_speed(self):
        """Convertit km/h en vitesse du jeu (plus haut km/h = plus rapide = moins de délai)"""
        # 50 km/h = 0.30s, 150 km/h = 0.02s (très rapide)
        self.speed = 0.30 - (self.speed_kmh - 50) * (0.28 / 100)

    def _spawn_car(self):
        """Génère une nouvelle voiture au hasard en haut"""
        lane = random.randint(0, self.num_lanes - 1)
        # Vérifier qu'il n'y a pas déjà une voiture trop proche
        for car in self.cars:
            if car.lane == lane and car.row < 5:
                return
        self.cars.append(Car(lane, 0))

    def _move_cars(self):
        """Déplace toutes les voitures vers le bas"""
        new_cars = []
        for car in self.cars:
            car.row += 1
            if car.row < self.height - 4:  # Garder dans l'écran
                new_cars.append(car)
            else:
                self.score += 1  # Voiture esquivée
        self.cars = new_cars

    def _check_collision(self):
        """Vérifie si le joueur a touché une voiture"""
        player_row = self.height - 5
        for car in self.cars:
            if car.lane == self.player_lane and car.row == player_row:
                return True
            # Vérifier aussi si la voiture chevauche
            if car.lane == self.player_lane and abs(car.row - player_row) <= 1:
                return True
        return False

    def _draw_road(self):
        """Dessine la route avec les voies"""
        h, w = self.stdscr.getmaxyx()
        self.height = h
        self.width = w

        # Position de la route (centrée)
        road_start_x = (w - self.road_width) // 2

        # Dessiner les bords de la route
        for row in range(2, h - 2):
            # Bord gauche
            if road_start_x - 1 >= 0:
                self.stdscr.attron(curses.color_pair(3))
                self.stdscr.addstr(row, road_start_x - 1, ROAD_EDGE)
                self.stdscr.attroff(curses.color_pair(3))

            # Bord droit
            if road_start_x + self.road_width < w:
                self.stdscr.attron(curses.color_pair(3))
                self.stdscr.addstr(row, road_start_x + self.road_width, ROAD_EDGE)
                self.stdscr.attroff(curses.color_pair(3))

            # Ligne médiane (entre les voies)
            for i in range(1, self.num_lanes):
                lane_sep_x = road_start_x + i * self.lane_width
                if lane_sep_x < w:
                    # Ligne discontinue
                    if row % 4 < 2:
                        self.stdscr.attron(curses.color_pair(4))
                        self.stdscr.addstr(row, lane_sep_x, ROAD_MARK)
                        self.stdscr.attroff(curses.color_pair(4))

    def _draw_player(self):
        """Dessine le joueur"""
        h, w = self.stdscr.getmaxyx()
        road_start_x = (w - self.road_width) // 2
        player_row = h - 5
        player_x = road_start_x + self.player_lane * self.lane_width + self.lane_width // 2

        if 0 <= player_row < h and 0 <= player_x < w:
            self.stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            self.stdscr.addstr(player_row, player_x, PLAYER_CHAR)
            self.stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

    def _draw_cars(self):
        """Dessine les voitures"""
        h, w = self.stdscr.getmaxyx()
        road_start_x = (w - self.road_width) // 2

        for car in self.cars:
            car_x = road_start_x + car.lane * self.lane_width + self.lane_width // 2
            if 0 <= car.row < h and 0 <= car_x < w:
                self.stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                self.stdscr.addstr(car.row, car_x, CAR_CHAR)
                self.stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)

    def _draw_ui(self):
        """Dessine l'interface utilisateur avec boutons tactiles"""
        h, w = self.stdscr.getmaxyx()

        # Titre
        title = "╔═══ DODGE CARS ═══╗"
        self.stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        self.stdscr.addstr(0, max(0, (w - len(title)) // 2), title)
        self.stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        # Score à gauche
        score_text = f"Score: {self.score}"
        self.stdscr.attron(curses.color_pair(5))
        self.stdscr.addstr(1, 2, score_text)
        self.stdscr.attroff(curses.color_pair(5))

        # Vitesse à droite
        speed_text = f"{self.speed_kmh} km/h"
        self.stdscr.attron(curses.color_pair(9) | curses.A_BOLD)
        if len(speed_text) + 2 < w:
            self.stdscr.addstr(1, w - len(speed_text) - 2, speed_text)
        self.stdscr.attroff(curses.color_pair(9) | curses.A_BOLD)

        # Boutons +/- pour vitesse
        btn_small_w = 5
        btn_small_h = 3
        speed_row = 2

        # Bouton -
        down_col = w - 14
        self.speed_down_btn = {"row": speed_row, "col": down_col, "height": btn_small_h, "width": btn_small_w}
        speed_border = curses.color_pair(9) | curses.A_BOLD

        if down_col >= 0:
            self.stdscr.attron(speed_border)
            self.stdscr.addstr(speed_row, down_col, "╔═══╗")
            self.stdscr.addstr(speed_row + 1, down_col, "║ - ║")
            self.stdscr.addstr(speed_row + 2, down_col, "╚═══╝")
            self.stdscr.attroff(speed_border)

        # Bouton +
        up_col = w - 8
        self.speed_up_btn = {"row": speed_row, "col": up_col, "height": btn_small_h, "width": btn_small_w}

        if up_col >= 0:
            self.stdscr.attron(speed_border)
            self.stdscr.addstr(speed_row, up_col, "╔═══╗")
            self.stdscr.addstr(speed_row + 1, up_col, "║ + ║")
            self.stdscr.addstr(speed_row + 2, up_col, "╚═══╝")
            self.stdscr.attroff(speed_border)

        # Nombre de voies à gauche
        lanes_text = f"{self.num_lanes} voies"
        self.stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
        self.stdscr.addstr(2, 2, lanes_text)
        self.stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)

        # Boutons +/- pour voies
        lanes_border = curses.color_pair(4) | curses.A_BOLD
        lanes_down_col = 2
        lanes_row = 3

        # Bouton - voies
        self.lanes_down_btn = {"row": lanes_row, "col": lanes_down_col, "height": btn_small_h, "width": btn_small_w}
        self.stdscr.attron(lanes_border)
        self.stdscr.addstr(lanes_row, lanes_down_col, "╔═══╗")
        self.stdscr.addstr(lanes_row + 1, lanes_down_col, "║ - ║")
        self.stdscr.addstr(lanes_row + 2, lanes_down_col, "╚═══╝")
        self.stdscr.attroff(lanes_border)

        # Bouton + voies
        lanes_up_col = 8
        self.lanes_up_btn = {"row": lanes_row, "col": lanes_up_col, "height": btn_small_h, "width": btn_small_w}
        self.stdscr.attron(lanes_border)
        self.stdscr.addstr(lanes_row, lanes_up_col, "╔═══╗")
        self.stdscr.addstr(lanes_row + 1, lanes_up_col, "║ + ║")
        self.stdscr.addstr(lanes_row + 2, lanes_up_col, "╚═══╝")
        self.stdscr.attroff(lanes_border)

        # Bouton GAUCHE
        btn_w = 10
        btn_h = 4
        btn_row = h - btn_h - 2

        left_col = 2
        self.left_btn = {"row": btn_row, "col": left_col, "height": btn_h, "width": btn_w}

        border_attr = curses.color_pair(8) | curses.A_BOLD
        fill_attr = curses.color_pair(8) | curses.A_BOLD

        # Bordure haute gauche
        if 0 <= btn_row < h:
            self.stdscr.attron(border_attr)
            self.stdscr.addstr(btn_row, left_col, "╔" + "═" * (btn_w - 2) + "╗")
            self.stdscr.attroff(border_attr)

        for r in range(btn_row + 1, btn_row + btn_h - 1):
            if 0 <= r < h:
                self.stdscr.attron(border_attr)
                self.stdscr.addstr(r, left_col, "║")
                self.stdscr.attroff(border_attr)
                self.stdscr.attron(fill_attr)
                self.stdscr.addstr(r, left_col + 1, " " * (btn_w - 2))
                self.stdscr.attroff(fill_attr)
                self.stdscr.attron(border_attr)
                self.stdscr.addstr(r, left_col + btn_w - 1, "║")
                self.stdscr.attroff(border_attr)

        row_bottom = btn_row + btn_h - 1
        if 0 <= row_bottom < h:
            self.stdscr.attron(border_attr)
            self.stdscr.addstr(row_bottom, left_col, "╚" + "═" * (btn_w - 2) + "╝")
            self.stdscr.attroff(border_attr)

        # Label gauche
        label_row = btn_row + btn_h // 2
        label_col = left_col + (btn_w - 3) // 2
        if 0 <= label_row < h:
            self.stdscr.attron(curses.A_BOLD)
            self.stdscr.addstr(label_row, label_col, " ◄ ")
            self.stdscr.attroff(curses.A_BOLD)

        # Bouton DROIT
        right_col = w - btn_w - 2
        self.right_btn = {"row": btn_row, "col": right_col, "height": btn_h, "width": btn_w}

        if 0 <= btn_row < h and right_col >= 0:
            self.stdscr.attron(border_attr)
            self.stdscr.addstr(btn_row, right_col, "╔" + "═" * (btn_w - 2) + "╗")
            self.stdscr.attroff(border_attr)

        for r in range(btn_row + 1, btn_row + btn_h - 1):
            if 0 <= r < h and right_col >= 0:
                self.stdscr.attron(border_attr)
                self.stdscr.addstr(r, right_col, "║")
                self.stdscr.attroff(border_attr)
                self.stdscr.attron(fill_attr)
                self.stdscr.addstr(r, right_col + 1, " " * (btn_w - 2))
                self.stdscr.attroff(fill_attr)
                self.stdscr.attron(border_attr)
                self.stdscr.addstr(r, right_col + btn_w - 1, "║")
                self.stdscr.attroff(border_attr)

        if 0 <= row_bottom < h and right_col >= 0:
            self.stdscr.attron(border_attr)
            self.stdscr.addstr(row_bottom, right_col, "╚" + "═" * (btn_w - 2) + "╝")
            self.stdscr.attroff(border_attr)

        # Label droit
        label_col_r = right_col + (btn_w - 3) // 2
        if 0 <= label_row < h and label_col_r >= 0:
            self.stdscr.attron(curses.A_BOLD)
            self.stdscr.addstr(label_row, label_col_r, " ► ")
            self.stdscr.attroff(curses.A_BOLD)

        # Contrôles
        controls = "q: Quitter"
        if len(controls) < w:
            self.stdscr.addstr(h - 1, max(0, (w - len(controls)) // 2), controls)

    def _draw_game_over(self):
        """Dessine l'écran de game over avec bouton restart tactile"""
        h, w = self.stdscr.getmaxyx()

        messages = [
            "╔═══════════════════════════╗",
            "║                           ║",
            "║      G A M E   O V E R    ║",
            "║                           ║",
            f"║    Score final: {self.score:>5}     ║",
            "║                           ║",
            "╚═══════════════════════════╝"
        ]

        start_row = h // 2 - len(messages) // 2 - 2
        for i, msg in enumerate(messages):
            row = start_row + i
            col = (w - len(msg)) // 2
            if 0 <= row < h and col >= 0:
                self.stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
                self.stdscr.addstr(row, col, msg)
                self.stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        # Bouton RESTART tactile
        btn_width = 25
        btn_height = 5
        btn_row = start_row + len(messages) + 2
        btn_col = (w - btn_width) // 2

        # Sauvegarder la zone du bouton pour détection tactile
        self.restart_btn = {
            "row": btn_row,
            "col": btn_col,
            "height": btn_height,
            "width": btn_width
        }

        # Dessiner le bouton avec bordure
        border_attr = curses.color_pair(7) | curses.A_BOLD
        fill_attr = curses.color_pair(7) | curses.A_BOLD

        # Bordure haute
        if 0 <= btn_row < h:
            self.stdscr.attron(border_attr)
            self.stdscr.addstr(btn_row, btn_col, "╔" + "═" * (btn_width - 2) + "╗")
            self.stdscr.attroff(border_attr)

        # Milieu du bouton
        for r in range(btn_row + 1, btn_row + btn_height - 1):
            if 0 <= r < h:
                self.stdscr.attron(border_attr)
                self.stdscr.addstr(r, btn_col, "║")
                self.stdscr.attroff(border_attr)

                self.stdscr.attron(fill_attr)
                self.stdscr.addstr(r, btn_col + 1, " " * (btn_width - 2))
                self.stdscr.attroff(fill_attr)

                self.stdscr.attron(border_attr)
                self.stdscr.addstr(r, btn_col + btn_width - 1, "║")
                self.stdscr.attroff(border_attr)

        # Bordure basse
        row_bottom = btn_row + btn_height - 1
        if 0 <= row_bottom < h:
            self.stdscr.attron(border_attr)
            self.stdscr.addstr(row_bottom, btn_col, "╚" + "═" * (btn_width - 2) + "╝")
            self.stdscr.attroff(border_attr)

        # Texte du bouton
        label = "↺  RECOMMENCER"
        label_row = btn_row + btn_height // 2
        label_col = btn_col + (btn_width - len(label)) // 2
        if 0 <= label_row < h and label_col >= 0:
            self.stdscr.attron(curses.A_BOLD | curses.A_REVERSE)
            self.stdscr.addstr(label_row, label_col, label)
            self.stdscr.attroff(curses.A_BOLD | curses.A_REVERSE)

        # Indication clavier
        hint = "(ou appuyez sur 'r')"
        hint_row = btn_row + btn_height + 1
        hint_col = (w - len(hint)) // 2
        if 0 <= hint_row < h:
            self.stdscr.addstr(hint_row, hint_col, hint)

    def _reset_game(self):
        """Remet le jeu à zéro"""
        self.player_lane = 1
        self.cars = []
        self.score = 0
        self.game_over = False
        self.speed_kmh = 80
        self._update_speed()
        self.spawn_rate = 0.03

    def _handle_input(self, key):
        """Gère les entrées utilisateur"""
        if self.game_over:
            if key == ord('r'):
                self._reset_game()
            elif key == ord('q'):
                self.running = False
            return

        if key == ord('a') or key == curses.KEY_LEFT:
            if self.player_lane > 0:
                self.player_lane -= 1
        elif key == ord('d') or key == curses.KEY_RIGHT:
            if self.player_lane < self.num_lanes - 1:
                self.player_lane += 1
        elif key == ord('q'):
            self.running = False
        elif key == ord('p'):
            self.paused = not self.paused

    def _handle_gpio_input(self, action):
        """Gère les entrées GPIO"""
        if self.game_over:
            return

        if action == "left" and self.player_lane > 0:
            self.player_lane -= 1
        elif action == "right" and self.player_lane < self.num_lanes - 1:
            self.player_lane += 1

    def _touch_to_rowcol(self, x_raw, y_raw, touch_reader):
        """Convertit les coordonnées brutes en lignes/colonnes du terminal"""
        h, w = self.stdscr.getmaxyx()

        dx = max(1, touch_reader.max_x - touch_reader.min_x)
        dy = max(1, touch_reader.max_y - touch_reader.min_y)

        x_norm = (x_raw - touch_reader.min_x) / dx
        y_norm = (y_raw - touch_reader.min_y) / dy

        col = int(x_norm * (w - 1))
        row = int(y_norm * (h - 1))

        row = max(0, min(h - 1, row))
        col = max(0, min(w - 1, col))
        return row, col

    def _handle_touch(self, x_raw, y_raw, touch_reader):
        """Gère un appui tactile"""
        row, col = self._touch_to_rowcol(x_raw, y_raw, touch_reader)

        if self.game_over:
            # Vérifier si on a touché le bouton restart
            btn = self.restart_btn
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                self._reset_game()
        else:
            # Vérifier bouton gauche
            btn = self.left_btn
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                if self.player_lane > 0:
                    self.player_lane -= 1
                return

            # Vérifier bouton droit
            btn = self.right_btn
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                if self.player_lane < self.num_lanes - 1:
                    self.player_lane += 1
                return

            # Vérifier bouton vitesse -
            btn = self.speed_down_btn
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                if self.speed_kmh > self.min_speed_kmh:
                    self.speed_kmh -= 5
                    self._update_speed()
                return

            # Vérifier bouton vitesse +
            btn = self.speed_up_btn
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                if self.speed_kmh < self.max_speed_kmh:
                    self.speed_kmh += 5
                    self._update_speed()
                return

            # Vérifier bouton voies -
            btn = self.lanes_down_btn
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                if self.num_lanes > self.min_lanes:
                    self.num_lanes -= 1
                    if self.player_lane >= self.num_lanes:
                        self.player_lane = self.num_lanes - 1
                return

            # Vérifier bouton voies +
            btn = self.lanes_up_btn
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                if self.num_lanes < self.max_lanes:
                    self.num_lanes += 1
                return

    def run(self):
        self.stdscr.nodelay(True)
        curses.curs_set(0)
        self._init_colors()

        last_update = 0
        frame_count = 0

        while self.running:
            now = time.time()

            # Mettre à jour les dimensions de l'écran
            try:
                self.height, self.width = self.stdscr.getmaxyx()
            except curses.error:
                pass

            # Gestion des entrées clavier
            try:
                key = self.stdscr.getch()
                if key != -1:
                    self._handle_input(key)
            except curses.error:
                pass

            # Gestion des entrées GPIO, tactiles et MQTT
            try:
                while not self.event_queue.empty():
                    event = self.event_queue.get_nowait()
                    if isinstance(event, str):
                        # Événement GPIO
                        self._handle_gpio_input(event)
                    elif isinstance(event, tuple):
                        if event[0] == "touch":
                            # Événement tactile
                            _, x_raw, y_raw = event
                            if hasattr(self, 'touch_reader') and self.touch_reader:
                                self._handle_touch(x_raw, y_raw, self.touch_reader)
                        elif event[0] == "speed":
                            # Potentiomètre vitesse (0-4095 → 50-150 km/h)
                            raw_val = event[1]
                            self.speed_kmh = int(50 + (raw_val / 4095) * 100)
                            self.speed_kmh = max(self.min_speed_kmh, min(self.max_speed_kmh, self.speed_kmh))
                            self._update_speed()
                        elif event[0] == "lanes":
                            # Potentiomètre voies (0-4095 → 2-8)
                            raw_val = event[1]
                            self.num_lanes = int(2 + (raw_val / 4095) * 6)
                            self.num_lanes = max(self.min_lanes, min(self.max_lanes, self.num_lanes))
                            if self.player_lane >= self.num_lanes:
                                self.player_lane = self.num_lanes - 1
            except Exception:
                pass

            if self.game_over:
                self.stdscr.erase()
                self._draw_road()
                self._draw_cars()
                self._draw_player()
                self._draw_ui()
                self._draw_game_over()
                self.stdscr.refresh()
                time.sleep(0.05)
                continue

            if self.paused:
                pause_msg = "PAUSE - Appuyez sur 'p' pour continuer"
                h, w = self.stdscr.getmaxyx()
                self.stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                self.stdscr.addstr(h // 2, max(0, (w - len(pause_msg)) // 2), pause_msg)
                self.stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                self.stdscr.refresh()
                time.sleep(0.05)
                continue

            # Mise à jour du jeu à intervalle régulier
            if now - last_update > self.speed:
                self._move_cars()

                # Spawn de nouvelles voitures - plus le score est élevé, plus il y en a
                # Base: 5%, +0.5% par point de score, max 40%
                dynamic_spawn = min(0.40, 0.05 + self.score * 0.005)
                if random.random() < dynamic_spawn:
                    self._spawn_car()

                last_update = now

            # Vérifier les collisions
            if self._check_collision():
                self.game_over = True

            # Dessiner
            self.stdscr.erase()
            self._draw_road()
            self._draw_cars()
            self._draw_player()
            self._draw_ui()
            self.stdscr.refresh()

            time.sleep(0.02)  # ~50 FPS max

def main(stdscr):
    event_queue = Queue()
    touch_reader = None
    mqtt_reader = None

    # Démarrer le lecteur de boutons GPIO (gpiozero utilise des callbacks)
    button_reader = ButtonReader(event_queue)

    # Démarrer le lecteur tactile
    if TOUCH_AVAILABLE:
        touch_reader = TouchReader(event_queue)
        if touch_reader.device:
            touch_reader.start()

    # Démarrer le lecteur MQTT
    if MQTT_AVAILABLE:
        mqtt_reader = MQTTReader(event_queue)

    game = DodgeGame(stdscr, event_queue)
    game.touch_reader = touch_reader
    game.run()

    # Nettoyage
    if mqtt_reader:
        mqtt_reader.stop()

if __name__ == "__main__":
    curses.wrapper(main)
