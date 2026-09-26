noms = ["Alice", "Bob", "Charlie"]
try:
    print(noms[9])
except IndexError:
    print("Erreur : La liste ne contient pas 10 prénoms.")