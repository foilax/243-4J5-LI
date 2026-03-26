#!/bin/bash
# Script pour lancer le jeu Dodge Cars sur l'écran tactile

echo "Lancement du jeu Dodge Cars sur l'écran tactile..."
echo "Pour revenir au bureau: Ctrl+Alt+F7 ou 'sudo chvt 7' depuis SSH"
echo ""
echo "Contrôles:"
echo "  - Bouton pin 36: Aller à gauche"
echo "  - Bouton pin 39: Aller à droite"
echo "  - Touche 'q': Quitter"
echo "  - Touche 'r': Recommencer après game over"
echo ""

# Passer sur tty1
sudo chvt 1

# Lancer le programme sur tty1
sudo setsid sh -c 'exec </dev/tty1 >/dev/tty1 2>&1 python3 /home/foilax/243-4J5-LI/labo/Labo-02/led-control/dodge_game.py'
