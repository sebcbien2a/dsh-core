Voici le script Python pur et simple qui ouvre le fichier 'fichier_fantome.txt' et affiche son contenu :


with open('fichier_fantome.txt', 'r') as fichier:
    contenu = fichier.read()
    print(contenu)