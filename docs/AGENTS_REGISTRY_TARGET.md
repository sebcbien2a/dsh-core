# Cartographie des Agents DSH-Core (Cible)

## RouterAgent
- **Mission :** Point d'entrée principal. Aiguillage des requêtes vagues et choix dynamique du modèle (Qwen, Claude) selon la complexité.

## ResearchAgent
- **Mission :** Moteur de renseignement. 
- **Outils :** Web (DDGS), Playwright (Scraping lourd), TOR (Darkweb), Qdrant (Recherche interne croisée).

## InvoiceAgent
- **Mission :** Traitement comptable automatisé.
- **Outils :** Surya OCR, Modèle de Vision (Qwen-VL), Pydantic (Validation mathématique).

## SkillAgent
- **Mission :** Création, publication et indexation de nouvelles compétences (code/scripts autonomes).

## MemoryAgent
- **Mission :** Gestion du contexte à long terme de l'utilisateur.
- **Outils :** Mem0, Qdrant, LlamaIndex.

## SupervisorAgent (Le "Cerveau" ultime)
- **Mission :** Réservé aux tâches complexes. Validation des données, apprentissage et auto-correction du système.
- **Modèle privilégié :** Claude 3.5 Sonnet ou Qwen 27B.
*Ajoute cet agent spécifique à ta liste pour qu'il devienne une entité à part entière.*

```markdown
## TeacherStudentAgent (ou SupervisorAgent)
- **Mission :** Piloter la montée en compétence du système DSH-Core.
- **Responsabilités :**
  - Détecter les échecs d'extraction répétés de Qwen 9B.
  - Déclencher l'escalade vers Qwen 27B (Niveau Expert).
  - Déclencher l'escalade vers Claude 3.5 (Niveau Superviseur).
  - Rédiger, valider et publier les nouvelles compétences dans le `skills_catalog`.
  - Alimenter la collection Qdrant `teacher_student_metrics` pour tracer l'apprentissage.
- **Objectif absolu :** Transformer progressivement les tâches coûteuses (Claude/27B) en compétences exécutables localement et rapidement par Qwen 9B.