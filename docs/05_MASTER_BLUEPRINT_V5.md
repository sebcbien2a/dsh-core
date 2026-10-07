START

   |
   V

RouterAgent

   |
   +-----------------------+
   |           |           |
   V           V           V

Research   Invoice     Coder

   |           |           |
   +-----------+-----------+
               |
               V

         MemoryAgent

               |
               V

        Validation OK ?

         |         |
        Oui       Non
         |         |
         V         V

      Retour   Qwen 27B

                    |
                    V

             Résolution ?

                |     |
               Oui   Non
                |     |
                V     V

             Retour Claude

                       |
                       V

                 TeacherStudent

                       |
                       V

                Nouveau Skill

                       |
                       V

                  Qdrant

                       |
                       V

                    Retour