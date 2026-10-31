---
master_doc: PROJECT_HANDOVER.md
branch_domain: Annexe historique — Archives numérotées
contract_version: 1.0.1
status: FROZEN
upstream_dependencies: []
downstream_consumers: []
verification_tests: []
---

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