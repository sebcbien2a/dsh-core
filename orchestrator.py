from typing import TypedDict
from langgraph.graph import StateGraph, END

# 1. Définition de l'état (la mémoire partagée durant une requête)
class AgentState(TypedDict):
    task: str
    complexity: str
    result: str
    status: str

# 2. Définition des Nœuds (Les fonctions exécutées par le graphe)
def node_triage(state: AgentState):
    """Analyse la requête pour déterminer le niveau de complexité."""
    # Simulation d'une logique de triage (à connecter à agents_registry.json plus tard)
    mots_cles_complexes = ["facture", "lettrage", "analyse", "code"]
    is_complex = any(mot in state["task"].lower() for mot in mots_cles_complexes)
    
    return {"complexity": "haute" if is_complex else "basse", "status": "trié"}

def node_base_model(state: AgentState):
    """Exécution par le modèle léger (ex: Qwen 9B)."""
    return {"result": f"Traitement standard terminé pour : {state['task']}", "status": "succès"}

def node_expert_model(state: AgentState):
    """Escalade vers le modèle lourd ou API externe (ex: Claude 3.5)."""
    return {"result": f"Traitement expert approfondi pour : {state['task']}", "status": "succès"}

# 3. Logique de Routage Conditionnel
def route_task(state: AgentState):
    """Décide du prochain nœud en fonction de la complexité."""
    if state["complexity"] == "haute":
        return "expert"
    return "base"

# 4. Construction et Compilation du Graphe
workflow = StateGraph(AgentState)

# Ajout des nœuds
workflow.add_node("triage", node_triage)
workflow.add_node("base_model", node_base_model)
workflow.add_node("expert_model", node_expert_model)

# Définition du flux
workflow.set_entry_point("triage")
workflow.add_conditional_edges(
    "triage", 
    route_task, 
    {"base": "base_model", "expert": "expert_model"}
)
workflow.add_edge("base_model", END)
workflow.add_edge("expert_model", END)

# Compilation du moteur
dsh_graph = workflow.compile()