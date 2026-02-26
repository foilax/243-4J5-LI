import threading
import time
from queue import Queue

import curses
from evdev import InputDevice, ecodes, list_devices
import paho.mqtt.client as mqtt
import ssl

# Configuration MQTT
from mqtt_config import MQTT_CONFIG


# ---------- GESTION DU TOUCH ----------

class TouchReader(threading.Thread):
    def __init__(self, event_queue: Queue):
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.device = self._find_touch_device()
        if not self.device:
            raise RuntimeError("Aucun périphérique touchscreen trouvé.")

        # On récupère les infos d'axes pour calibrer
        abs_x = self.device.absinfo(ecodes.ABS_MT_POSITION_X)
        abs_y = self.device.absinfo(ecodes.ABS_MT_POSITION_Y)

        self.min_x, self.max_x = abs_x.min, abs_x.max
        self.min_y, self.max_y = abs_y.min, abs_y.max

        self.current_x = (self.min_x + self.max_x) // 2
        self.current_y = (self.min_y + self.max_y) // 2

    def _find_touch_device(self):
        """
        Essaie de trouver un device dont le nom contient 'touch' ou 'ft5406'
        (fréquent sur les écrans Raspberry Pi).
        """
        for path in list_devices():
            dev = InputDevice(path)
            name = dev.name.lower()
            if "touch" in name or "ft5406" in name:
                print(f"[TouchReader] Using device: {dev.name} ({path})")
                return dev
        return None

    def run(self):
        for event in self.device.read_loop():
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_MT_POSITION_X:
                    self.current_x = event.value
                elif event.code == ecodes.ABS_MT_POSITION_Y:
                    self.current_y = event.value

            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                # 1 = touch down, 0 = touch up
                if event.value == 1:
                    # On push un "tap" dans la queue avec les coordonnées brutes
                    self.event_queue.put(("tap", self.current_x, self.current_y))


# ---------- UI CURSES ----------

class LEDControlUI:
    def __init__(self, stdscr, touch_reader: TouchReader, event_queue: Queue, mqtt_config: dict):
        self.stdscr = stdscr
        self.touch_reader = touch_reader
        self.event_queue = event_queue
        self.running = True
        self.status_message = "Prêt - Contrôle des LEDs via MQTT"

        # Configuration MQTT
        self.mqtt_config = mqtt_config
        self.mqtt_client = None
        self.mqtt_connected = False

        # Topics MQTT pour les LEDs du LilyGo
        device_id = mqtt_config.get("device_id", "esp32-XXXX")
        self.led1_topic = f"{device_id}/led/1/set"
        self.led2_topic = f"{device_id}/led/2/set"

        self._init_mqtt()

        self.buttons = []  # rempli à chaque redraw en fonction de la taille écran

        # États des LEDs (pour les toggle switches)
        self.led1_state = False  # False = OFF, True = ON
        self.led2_state = False

        # Buffer pour les messages MQTT reçus (max 10 lignes)
        self.mqtt_feedback = []
        self.max_feedback_lines = 10

    def _init_mqtt(self):
        """
        Initialise la connexion MQTT via WebSocket Secure (WSS)
        """
        try:
            # Créer le client MQTT avec transport WebSocket
            client_id = f"python-control-{int(time.time())}"
            self.mqtt_client = mqtt.Client(
                client_id=client_id,
                transport="websockets"
            )

            # Configuration SSL pour WSS
            self.mqtt_client.tls_set(
                ca_certs=None,
                certfile=None,
                keyfile=None,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS,
                ciphers=None
            )

            # Authentification
            username = self.mqtt_config.get("username", "esp_user")
            password = self.mqtt_config.get("password", "")
            self.mqtt_client.username_pw_set(username, password)

            # Callbacks
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            self.mqtt_client.on_message = self._on_mqtt_message

            # Connexion
            broker = self.mqtt_config.get("broker", "mqtt.edxo.ca")
            port = self.mqtt_config.get("port", 443)

            self.status_message = f"Connexion à {broker}:{port}..."
            self.mqtt_client.connect(broker, port, 60)

            # Démarrer la boucle réseau dans un thread
            self.mqtt_client.loop_start()

        except Exception as e:
            self.status_message = f"Erreur MQTT: {str(e)}"
            self.mqtt_client = None

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback appelé lors de la connexion MQTT"""
        if rc == 0:
            self.mqtt_connected = True
            self.status_message = "MQTT connecté!"
            self._add_feedback("✓ Connecté au broker MQTT")

            # S'abonner aux topics de statut des boutons (optionnel)
            device_id = self.mqtt_config.get("device_id", "esp32-XXXX")
            button1_topic = f"{device_id}/button/1/state"
            button2_topic = f"{device_id}/button/2/state"
            client.subscribe(button1_topic)
            client.subscribe(button2_topic)

        else:
            self.mqtt_connected = False
            error_messages = {
                1: "Protocole incorrect",
                2: "Client ID rejeté",
                3: "Serveur indisponible",
                4: "Username/Password incorrect",
                5: "Non autorisé"
            }
            msg = error_messages.get(rc, f"Erreur inconnue ({rc})")
            self.status_message = f"Échec connexion MQTT: {msg}"
            self._add_feedback(f"✗ Erreur: {msg}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """Callback appelé lors de la déconnexion MQTT"""
        self.mqtt_connected = False
        if rc != 0:
            self.status_message = f"Déconnexion MQTT inattendue (code {rc})"
            self._add_feedback("⚠ Connexion perdue, reconnexion...")

    def _on_mqtt_message(self, client, userdata, msg):
        """Callback appelé lors de la réception d'un message MQTT"""
        topic = msg.topic
        payload = msg.payload.decode('utf-8', errors='ignore')
        self._add_feedback(f"← {topic}: {payload}")

    def _add_feedback(self, message):
        """Ajoute un message au buffer de feedback"""
        self.mqtt_feedback.append(message)
        if len(self.mqtt_feedback) > self.max_feedback_lines:
            self.mqtt_feedback.pop(0)

    def _publish_mqtt(self, topic, message):
        """
        Publie un message MQTT
        """
        if self.mqtt_client and self.mqtt_connected:
            try:
                result = self.mqtt_client.publish(topic, message, qos=0)
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    self.status_message = f"Envoyé: {topic} = {message}"
                    self._add_feedback(f"→ {topic}: {message}")
                else:
                    self.status_message = f"Erreur publication: {result.rc}"
            except Exception as e:
                self.status_message = f"Erreur: {str(e)}"
        else:
            self.status_message = "MQTT non connecté"

    def _draw_big_text(self, text, start_row, center_col, attr):
        """
        Dessine du texte en ASCII art 3x5 (chaque lettre fait 3 colonnes x 5 lignes)
        """
        # Police ASCII art simplifiée pour ON/OFF
        font = {
            'O': [
                "███",
                "█ █",
                "█ █",
                "█ █",
                "███"
            ],
            'N': [
                "███",
                "█ █",
                "█ █",
                "█ █",
                "█ █"
            ],
            'F': [
                "███",
                "█  ",
                "██ ",
                "█  ",
                "█  "
            ],
            ' ': [
                "   ",
                "   ",
                "   ",
                "   ",
                "   "
            ]
        }

        # Calculer la largeur totale
        total_width = len(text) * 4  # 3 pour la lettre + 1 d'espacement
        start_col = center_col - total_width // 2

        # Dessiner chaque ligne
        h, w = self.stdscr.getmaxyx()
        for line_idx in range(5):
            row = start_row + line_idx
            if 0 <= row < h:
                col = start_col
                line_text = ""
                for char in text.upper():
                    if char in font:
                        line_text += font[char][line_idx] + " "
                    else:
                        line_text += "    "

                if col >= 0 and col + len(line_text) < w:
                    self.stdscr.attron(attr)
                    self.stdscr.addstr(row, col, line_text)
                    self.stdscr.attroff(attr)

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_YELLOW)   # bouton QUIT (jaune vif)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_GREEN)    # bouton actif
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_YELLOW)   # texte status (jaune sur noir)
        curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_RED)     # LED ROUGE ON (jaune sur rouge)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_GREEN)    # LED VERTE ON (noir sur vert)
        curses.init_pair(6, curses.COLOR_YELLOW, curses.COLOR_BLUE)    # LED ROUGE OFF (jaune sur bleu foncé)
        curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)     # LED VERTE OFF (blanc sur bleu foncé)
        curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_BLACK)    # Bordures blanches sur noir
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_CYAN)     # Titre (noir sur cyan)

    def _build_buttons(self, h, w):
        """
        Construit 3 boutons: LED1 et LED2 côte à côte, et QUIT en bas
        """
        self.buttons = []
        
        # Calcul des dimensions pour côte à côte
        btn_width = max(20, min(40, (w - 12) // 2))
        btn_height = 10
        quit_height = 5
        
        col_spacing = 4
        total_w = btn_width * 2 + col_spacing
        start_col = (w - total_w) // 2
        
        # Position verticale centrée
        total_height = btn_height + 2 + quit_height
        start_row = max(6, (h - total_height) // 2 + 2)

        # Bouton LED 1 (Gauche)
        self.buttons.append({
            "name": "LED1",
            "label": "ROUGE",
            "state_attr": "led1_state",
            "row": start_row,
            "col": start_col,
            "height": btn_height,
            "width": btn_width,
            "active": False,
            "topic": self.led1_topic,
            "color_on": 4,   # Rouge
            "color_off": 6,  # Bleu/gris
        })

        # Bouton LED 2 (Droite)
        self.buttons.append({
            "name": "LED2",
            "label": "VERTE",
            "state_attr": "led2_state",
            "row": start_row,
            "col": start_col + btn_width + col_spacing,
            "height": btn_height,
            "width": btn_width,
            "active": False,
            "topic": self.led2_topic,
            "color_on": 5,   # Vert
            "color_off": 7,  # Bleu/gris
        })

        # Bouton QUIT (En bas, prend toute la largeur)
        self.buttons.append({
            "name": "QUIT",
            "label": "QUITTER",
            "state_attr": None,
            "row": start_row + btn_height + 2,
            "col": start_col,
            "height": quit_height,
            "width": total_w,
            "active": False,
            "topic": None,
            "color_on": 1,
            "color_off": 1,
        })

    def _draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        # Titre ÉNORME avec contraste élevé
        title_line1 = "█████████████████████████████████████████"
        title_line2 = "███   C O N T R Ô L E   L E D s   ███"
        title_line3 = "█████████████████████████████████████████"

        self.stdscr.attron(curses.color_pair(9) | curses.A_BOLD)  # Cyan sur noir
        if len(title_line1) < w:
            self.stdscr.addstr(0, max(0, (w - len(title_line1)) // 2), title_line1)
        if len(title_line2) < w:
            self.stdscr.addstr(1, max(0, (w - len(title_line2)) // 2), title_line2)
        self.stdscr.attroff(curses.color_pair(9) | curses.A_BOLD)

        # Indicateur de connexion - ÉNORME et très visible avec contraste élevé
        if self.mqtt_connected:
            conn_status = "▓▓▓  M Q T T   C O N N E C T É  ▓▓▓"
            conn_color = curses.color_pair(5) | curses.A_BOLD  # Noir sur vert
        else:
            conn_status = "▓▓▓  M Q T T   D É C O N N E C T É  ▓▓▓"
            conn_color = curses.color_pair(4) | curses.A_BOLD  # Jaune sur rouge

        self.stdscr.attron(conn_color)
        if len(conn_status) < w:
            self.stdscr.addstr(2, max(0, (w - len(conn_status)) // 2), conn_status)
        self.stdscr.attroff(conn_color)

        # Zone de feedback MQTT (coin supérieur droit) - Contraste élevé
        feedback_title = "╔══ MQTT DEBUG ══╗"
        feedback_start_row = 4
        if w > 80:
            feedback_col = w - 38
        else:
            feedback_col = 2

        # Titre en cyan sur noir pour meilleure visibilité
        self.stdscr.attron(curses.color_pair(9) | curses.A_BOLD)
        if feedback_col + len(feedback_title) < w:
            self.stdscr.addstr(feedback_start_row, feedback_col, feedback_title[:w - feedback_col - 1])
        self.stdscr.attroff(curses.color_pair(9) | curses.A_BOLD)

        # Afficher les 5 derniers messages MQTT avec contraste
        for i, msg in enumerate(self.mqtt_feedback[-5:]):
            row = feedback_start_row + 1 + i
            if row < h - 3 and feedback_col < w:
                display_msg = msg[:min(35, w - feedback_col - 1)]
                # Messages en blanc brillant
                self.stdscr.attron(curses.A_BOLD)
                self.stdscr.addstr(row, feedback_col, display_msg)
                self.stdscr.attroff(curses.A_BOLD)

        # Status bar en bas - TRÈS GROS et TRÈS VISIBLE avec contraste élevé
        status_msg = self.status_message[:w-10]  # Limiter à la largeur
        status_bar = f"▶▶▶  {status_msg}  ◀◀◀"

        # Noir sur jaune (très contrasté!)
        self.stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
        if len(status_bar) < w:
            self.stdscr.addstr(h - 2, max(0, (w - len(status_bar)) // 2), status_bar[:w-2])
        self.stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)

        # Ligne d'aide - en bas
        help_text = "Touchez l'écran ou appuyez sur 'q'"
        if len(help_text) < w:
            self.stdscr.addstr(h - 1, max(0, (w - len(help_text)) // 2), help_text)

        # Construire les boutons
        self._build_buttons(h, w)

        # Dessin des boutons toggle avec bordures
        for btn in self.buttons:
            # Déterminer l'état du bouton (ON/OFF) pour les LEDs
            is_on = False
            if btn["state_attr"]:
                is_on = getattr(self, btn["state_attr"], False)

            # Choisir la couleur selon l'état
            color_pair = btn["color_on"] if is_on else btn["color_off"]
            attr = curses.color_pair(color_pair)
            attr |= curses.A_BOLD  # Toujours en gras

            # Dessiner la bordure blanche ÉPAISSE avec double lignes
            border_attr = curses.color_pair(8) | curses.A_BOLD  # Blanc sur noir
            row_top = btn["row"]
            row_bottom = btn["row"] + btn["height"] - 1
            col_left = btn["col"]
            col_right = btn["col"] + btn["width"] - 1

            # Ligne du haut DOUBLE
            if 0 <= row_top < h:
                self.stdscr.attron(border_attr)
                self.stdscr.addstr(row_top, col_left, "╔" + "═" * (btn["width"] - 2) + "╗")
                self.stdscr.attroff(border_attr)

            # Lignes du milieu avec fond coloré
            for r in range(row_top + 1, row_bottom):
                if 0 <= r < h:
                    # Bordure gauche DOUBLE
                    self.stdscr.attron(border_attr)
                    self.stdscr.addstr(r, col_left, "║")
                    self.stdscr.attroff(border_attr)

                    # Fond coloré
                    self.stdscr.attron(attr)
                    self.stdscr.addstr(r, col_left + 1, " " * (btn["width"] - 2))
                    self.stdscr.attroff(attr)

                    # Bordure droite DOUBLE
                    self.stdscr.attron(border_attr)
                    self.stdscr.addstr(r, col_right, "║")
                    self.stdscr.attroff(border_attr)

            # Ligne du bas DOUBLE
            if 0 <= row_bottom < h:
                self.stdscr.attron(border_attr)
                self.stdscr.addstr(row_bottom, col_left, "╚" + "═" * (btn["width"] - 2) + "╝")
                self.stdscr.attroff(border_attr)

            # Afficher le label du bouton en GROS et GRAS
            label = "  " + btn['label'] + "  "  # Espaces de padding

            label_col = btn["col"] + max(0, (btn["width"] - len(label)) // 2)
            label_row = btn["row"] + 2  # Position fixe près du haut
            if 0 <= label_row < h and label_col + len(label) < w:
                self.stdscr.attron(attr | curses.A_UNDERLINE)
                self.stdscr.addstr(label_row, label_col, label)
                self.stdscr.attroff(attr | curses.A_UNDERLINE)

            # Afficher l'état ON/OFF en VRAIMENT GROS (ASCII art multi-lignes)
            if btn["state_attr"]:  # Uniquement pour les LEDs
                state_row = btn["row"] + btn["height"] // 2 - 2
                center_col = btn["col"] + btn["width"] // 2

                if is_on:
                    # Dessiner "ON" en gros ASCII art
                    self._draw_big_text("ON", state_row, center_col, attr | curses.A_REVERSE)
                else:
                    # Dessiner "OFF" en gros ASCII art
                    self._draw_big_text("OFF", state_row, center_col, attr)
            else:
                # Pour le bouton QUIT - texte centré en GROS avec contraste max
                quit_row = btn["row"] + btn["height"] // 2 - 2
                quit_text1 = "╔═════════════════════╗"
                quit_text2 = "║  CLIQUEZ ICI POUR  ║"
                quit_text3 = "║                    ║"
                quit_text4 = "║   Q U I T T E R    ║"
                quit_text5 = "╚═════════════════════╝"

                for i, text in enumerate([quit_text1, quit_text2, quit_text3, quit_text4, quit_text5]):
                    row = quit_row + i
                    col = btn["col"] + max(0, (btn["width"] - len(text)) // 2)
                    if 0 <= row < h and col + len(text) < w:
                        # Noir sur jaune pour maximum de contraste
                        self.stdscr.attron(attr)
                        self.stdscr.addstr(row, col, text)
                        self.stdscr.attroff(attr)

        self.stdscr.refresh()

    def _touch_to_rowcol(self, x_raw, y_raw):
        """
        Map coordonnées brutes evdev -> lignes/colonnes du terminal curses.
        """
        h, w = self.stdscr.getmaxyx()

        # protection division par zéro
        dx = max(1, self.touch_reader.max_x - self.touch_reader.min_x)
        dy = max(1, self.touch_reader.max_y - self.touch_reader.min_y)

        x_norm = (x_raw - self.touch_reader.min_x) / dx
        y_norm = (y_raw - self.touch_reader.min_y) / dy

        col = int(x_norm * (w - 1))
        row = int(y_norm * (h - 1))

        # clamp
        row = max(0, min(h - 1, row))
        col = max(0, min(w - 1, col))
        return row, col

    def _handle_touch_tap(self, x_raw, y_raw):
        row, col = self._touch_to_rowcol(x_raw, y_raw)

        # Vérifier sur quel bouton on a tapé
        clicked_btn = None
        for btn in self.buttons:
            if (btn["row"] <= row < btn["row"] + btn["height"] and
                    btn["col"] <= col < btn["col"] + btn["width"]):
                clicked_btn = btn
                break

        if not clicked_btn:
            self.status_message = f"Touché hors bouton: row={row}, col={col}"
            return

        # Traiter le clic selon le bouton
        btn_name = clicked_btn["name"]

        if btn_name == "QUIT":
            self.status_message = "Arrêt demandé..."
            self.running = False
        elif btn_name == "LED1":
            # Toggle LED1
            self.led1_state = not self.led1_state
            message = "ON" if self.led1_state else "OFF"
            self._publish_mqtt(clicked_btn["topic"], message)
            self.status_message = f"LED ROUGE: {message}"
        elif btn_name == "LED2":
            # Toggle LED2
            self.led2_state = not self.led2_state
            message = "ON" if self.led2_state else "OFF"
            self._publish_mqtt(clicked_btn["topic"], message)
            self.status_message = f"LED VERTE: {message}"

    def run(self):
        self.stdscr.nodelay(True)
        curses.curs_set(0)
        self._init_colors()

        last_redraw = 0

        while self.running:
            now = time.time()
            if now - last_redraw > 0.05:  # ~20 FPS
                self._draw()
                last_redraw = now

            # Lecture touches clavier
            try:
                ch = self.stdscr.getch()
            except curses.error:
                ch = -1

            if ch == ord('q'):
                self.status_message = "Quit avec 'q'."
                self.running = False

            # Gestion des événements tactiles
            try:
                event = self.event_queue.get_nowait()
            except Exception:
                event = None

            if event:
                kind, x_raw, y_raw = event
                if kind == "tap":
                    self._handle_touch_tap(x_raw, y_raw)

            time.sleep(0.01)

        # Fermer la connexion MQTT à la fin
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()


# ---------- ENTRY POINT ----------

def main(stdscr):
    event_queue = Queue()
    touch_reader = TouchReader(event_queue)
    touch_reader.start()

    ui = LEDControlUI(stdscr, touch_reader, event_queue, MQTT_CONFIG)
    ui.run()


if __name__ == "__main__":
    curses.wrapper(main)
