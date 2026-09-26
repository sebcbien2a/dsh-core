# router.py - Routeur d'agents V2 (Cascade locale, Qwen2.5-VL & Superviseur Claude)
import re
from typing import Any, Dict

# ==========================================
# 1. REGISTRE DES MODÈLES (FACILE À MODIFIER)
# ==========================================
MODELS_REGISTRY = {
    "vision_general": "qwen2.5vl:7b",       # Capteur visuel / OCR rapide
    "executor_fast": "qwen3.5:9b",          # Tâches courantes, scripts rapides, MCP
    "reasoning_expert": "qwen3.6:27b",      # Raisonnement lourd, refactoring complexe
    "supervisor_cloud": "anthropic/claude-3-5-sonnet" # Audit, architecture, validation de SKILL
}

# Import sécurisé du gestionnaire unifié
try:
    from memory_manager import recuperer_contexte, find_skill
    HAS_MEMORY_MANAGER = True
except ImportError:
    HAS_MEMORY_MANAGER = False

def detecter_complexite(prompt: str) -> str:
    """Évalue si la tâche locale nécessite l'expert Qwen 27B ou l'exécuteur 9B."""
    prompt_lower = prompt.lower()
    
    expert_patterns = [
        r'\b(optimis|refactor|complex|algorithme|debug.*avancé)\b',
        r'\b(architecture.*données|parcours.*graphe|concurren)\b',
        r'\b(analyse.*critique|modélis|pipeline.*comptable)\b'
    ]
    if any(re.search(p, prompt_lower) for p in expert_patterns):
        return "expert"
    return "fast"

def analyze_prompt_complexity(prompt: str, image_provided: bool = False) -> Dict[str, str]:
    """Détermine le niveau hiérarchique requis selon la nature du prompt."""
    prompt_lower = prompt.lower()

    # 1. Flux Multimodal / Vision (Aiguillage général)
    vision_patterns = [r'\b(image|photo|capture|scann|vision|vois-tu|décris)\b']
    if image_provided or any(re.search(p, prompt_lower) for p in vision_patterns):
        return {
            "agent": "vision-sensor-local",
            "model": MODELS_REGISTRY["vision_general"],
            "role": "Capteur Visuel (Multimodal)",
            "tier": "vision",
            "reasoning": "Présence d'une image ou requête d'observation visuelle générale."
        }

    # 2. Superviseur Cloud (Audit, gouvernance, rédaction de règles / SKILL)
    teacher_patterns = [
        r'\b(skill|compétence|mémoire procédurale|template)\b',
        r'\b(juge|audit.*sécurité|valide.*code|contrôle.*qualité)\b',
        r'\b(arbitre|master.*blueprint|décision.*architecture)\b'
    ]
    if any(re.search(p, prompt_lower) for p in teacher_patterns):
        return {
            "agent": "cloud-supervisor",
            "model": MODELS_REGISTRY["supervisor_cloud"],
            "role": "Superviseur & Rédacteur SKILL",
            "tier": "supervisor",
            "reasoning": "Tâche d'arbitrage de haut niveau, audit de code ou gouvernance du système."
        }

    # 3. Cascade Locale (Expert 27B vs Exécuteur 9B)
    if detecter_complexite(prompt) == "expert":
        return {
            "agent": "qwen-expert-local",
            "model": MODELS_REGISTRY["reasoning_expert"],
            "role": "Expert Raisonnement Local",
            "tier": "expert",
            "reasoning": "Tâche locale nécessitant une capacité d'abstraction et d'analyse avancée."
        }

    return {
        "agent": "qwen-executor-local",
        "model": MODELS_REGISTRY["executor_fast"],
        "role": "Exécuteur Rapide Local",
        "tier": "fast",
        "reasoning": "Tâche opérationnelle directe, manipulation d'outils MCP ou script rapide."
    }

def route_prompt(prompt: str, user_id: str = "admin", image_provided: bool = False) -> Dict[str, Any]:
    """Aiguille la requête et injecte les souvenirs et compétences applicables."""
    decision = analyze_prompt_complexity(prompt, image_provided)
    
    # 1. Vérification d'une compétence outillée (Skill)
    matched_skill = None
    if HAS_MEMORY_MANAGER:
        matched_skill = find_skill(prompt)

    # 2. Récupération du contexte contextuel utilisateur
    contexte_utilisateur = ""
    if HAS_MEMORY_MANAGER:
        contexte_utilisateur = recuperer_contexte(user_id, prompt)

    # 3. Assemblage du contrat d'instructions
    instructions = {
        "vision": "Tu es le Capteur Visuel. Analyse les éléments graphiques, extrait le texte visible et décris la scène avec précision.",
        "supervisor": "Tu es le Superviseur. Audite les résultats, valide le respect des normes et génère les fiches SKILL.",
        "expert": "Tu es l'Expert Local. Résous les blocages logiques complexes, optimise le code et garantis la cohérence technique.",
        "fast": "Tu es l'Exécuteur Rapide. Utilise les outils locaux (MCP, fichiers, scripts) pour exécuter l'action demandée sans détour."
    }

    return {
        **decision,
        "instructions": instructions[decision["tier"]],
        "active_skill": matched_skill,
        "episodic_context": contexte_utilisateur,
        "input_prompt": prompt
    }

if __name__ == "__main__":
    print("=== TEST DU ROUTEUR V2 (Qwen2.5-VL Ready) ===")

    scenarios = [
        ("Décris ce qui est présent sur cette capture d'écran.", True),
        ("Rédige un nouveau template de SKILL pour auditer la mémoire.", False),
        ("Optimise cet algorithme récursif pour éviter les fuites de mémoire.", False),
        ("Crée le fichier script.py et écris une fonction de test.", False),
        ("Vérifie la disponibilité de Qdrant avec un ping.", False)
    ]

    for p, img in scenarios:
        res = route_prompt(prompt=p, image_provided=img)
        print(f"\nPrompt : « {p} » (Image : {img})")
        print(f"-> Agent assigné : {res['agent']} ({res['model']})")
        print(f"-> Rôle : {res['role']}")
        print(f"-> Justification : {res['reasoning']}")
        if res.get("active_skill"):
            print(f"-> Skill détecté : {res['active_skill']['name']}")