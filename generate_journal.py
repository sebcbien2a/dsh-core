# generate_journal.py - Générateur du journal d'installation V2 de dsh-core
from datetime import datetime
from pathlib import Path

JOURNAL_PATH = Path(r"A:\dsh-core\knowledge\03_systeme_cor\JOURNAL_INSTALLATION_V2.md")

JOURNAL_CONTENT = f"""# JOURNAL D'INSTALLATION & DE VALIDATION V2 - DSH-CORE
*Généré automatiquement le {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*

---

## 1. Environnement & Socle Système
- **Interpréteur :** Python 3.11.9 (environnement virtuel isolé dans `.venv-v3`).
- **Assainissement :** Suppression des anciens environnements conflictuels (`.venv`, `.venv-1`, `.venv-v2`) basés sur Python 3.14.
- **Stockage disque :** Déport complet des modèles Ollama et conteneurs sur le lecteur `A:\\`.

---

## 2. Infrastructure Vectorielle & Données (Docker / Qdrant)
- **Moteur :** Qdrant déployé via Docker (`http://localhost:6333`).
- **Persistance :** Volumes montés sur le lecteur `A:\\`.
- **Collections validées :**
  - `mem0` : calibrée sur 768 dimensions (`nomic-embed-text:latest`).
  - `document_bus` : collection LlamaIndex pour le RAG technique.

---

## 3. Gestionnaire de Mémoire Unifié (`memory_manager.py`)
- **Mémoire épisodique (Mem0) :**
  - Extraction de faits utilisateurs avec `qwen3.5:9b`.
  - Stockage vectoriel direct dans Qdrant (768 dims).
  - Validation : extraction et restitution de faits confirmées.
- **Bus Documentaire (LlamaIndex) :**
  - Indexation et requêtage RAG branchés sur la collection `document_bus`.
  - Timeout de sécurité fixé à 120 s pour absorber les temps de swap VRAM sur GPU 8 Go.
- **Catalogue de Compétences (Skills) :**
  - Registre persistant `A:\\dsh-core\\skills\\registry.json`.
  - Détection automatique et synchronisation validée (ex: skill `ping_qdrant`).

---

## 4. Routeur d'Agents & Cascade V2 (`router.py`)
- **Modèle Vision Généraliste :**
  - Remplacement de Llama Vision par **`qwen2.5vl:7b`** (meilleur ratio VRAM/qualité, spécialisé documents et tableaux).
  - Stockage validé sur `A:\\models`.
- **Cascade d'Exécution Locale :**
  - **Tier Rapide :** `qwen3.5:9b` pour l'exécution directe, les scripts MCP et le système de fichiers.
  - **Tier Expert :** `qwen3.6:27b` déclenché automatiquement sur détection de logique complexe ou refactoring.
- **Supervision Cloud :**
  - `Claude 3.5 Sonnet` réservé à l'arbitrage d'architecture, l'audit de code et la rédaction de fiches de compétences (`SKILL.md`).
- **Aiguillage outillé :**
  - Détection automatique des compétences déclarées (`find_skill`).
  - Injection du contexte épisodique (`recuperer_contexte`) en amont de l'instruction agent.

---

## 5. Prochaine Étape : Phase 8 (Pipeline Métier Facturation)
- Workflow comptable spécialisé et indépendant du routeur général.
- Stack cible : **Surya OCR / Docling** (OCR déterministe) + **Document Vision Model** + **Agent de Réparation OCR** (Qwen 9B) + Validation stricte JSON Pydantic + Intégration n8n.
"""

def main():
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
        f.write(JOURNAL_CONTENT)
    print(f" Structure du journal mise a jour.")
    print(f" Succes ! Journal consigne : {JOURNAL_PATH}")

if __name__ == "__main__":
    main()