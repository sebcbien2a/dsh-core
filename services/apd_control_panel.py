"""P24 - Human-readable HTML control panel for the APD accounting cockpit.

The local web server must never dump raw JSON to the director. This module
renders a clean French dashboard with one button per stage (1-4), a status
card, a pending-questions table and a readable stage report. The server still
exposes the stable JSON endpoints used by Hermes / Telegram, but the browser
page consumes them and paints real HTML tables.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any, Iterable, Mapping

from schemas.accounting_choices import (
    CATEGORY_CHOICES,
    DECISION_GROUPS,
    DECISION_INTERNAL_CHOICES,
    DECISION_SIMPLE_CHOICES,
    EXERCISE_CHOICES,
    PAYMENT_SOURCE_CHOICES,
    SUBCATEGORY_CHOICES,
    as_dict,
    normalize_category,
)
from services.cockpit_decision_service import (
    DECISION_GROUP_TARGETS,
    INTERNAL_DECISION_TARGETS,
    decision_menu_for,
    full_decision_choices,
)

STAGE_LABELS: tuple[tuple[int, str, str], ...] = (
    (1, "Étape 1 - Vision & Tableau Mère", "Analyser les pièces et valider les évidences"),
    (2, "Étape 2 - Rapprochement bancaire", "Lettrer les débits et encaissements Caisse d'Épargne"),
    (3, "Étape 3 - Ventilation & cohérence", "Rafraîchir les classeurs et vérifier les liens PDF"),
    (4, "Étape 4 - Simulations TVA & Bilan", "Produire le CA12 et le bilan prévisionnel"),
)
STAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("processed", "Pièces traitées"),
    ("validated", "Pièces validées"),
    ("pending", "Questions en attente"),
    ("moved", "Fichiers déplacés"),
    ("imported_ofx", "Lignes OFX importées"),
    ("reconciled", "Rapprochements"),
    ("status", "Statut"),
)
STATUS_FIELDS: tuple[tuple[str, str], ...] = (
    ("tenant", "Dossier"),
    ("year", "Exercice"),
    ("pending_questions", "Questions en attente"),
    ("pending", "Pièces en attente"),
    ("validated", "Pièces validées"),
    ("rejected", "Pièces rejetées"),
    ("store_pending", "Écritures en attente"),
    ("tenant_dir", "Répertoire de travail"),
    ("export_dir", "Répertoire des classeurs"),
    ("readable_workbook", "Classeur lisible"),
)
PENDING_FIELDS: tuple[tuple[str, str], ...] = (
    ("date_piece", "Date"),
    ("source", "Source"),
    ("fichier_ref", "Fichier / Réf"),
    ("fournisseur", "Fournisseur / Tiers"),
    ("libelle", "Libellé / N° Pièce"),
    ("montant_ttc", "Montant TTC (€)"),
    ("raison", "Pourquoi en attente"),
    ("action", "Action attendue"),
)
PENDING_ARBITRATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("categorie", "Catégorie"),
    ("sous_categorie", "Sous-Catégorie"),
    ("exercice", "Exercice Année"),
    ("source_paiement", "Source de paiement"),
    ("decision", "Décision"),
)
PAGE_TITLE = "APD - Cockpit comptable P27"


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return escape(str(value))


def render_value_table(
    rows: Iterable[tuple[str, Any]], *, table_class: str = "data"
) -> str:
    """Render a two-column French key/value HTML table."""
    body = "\n".join(
        f"<tr><th>{_cell(label)}</th><td>{_cell(value)}</td></tr>"
        for label, value in rows
    )
    return (
        f'<table class="{table_class}">'
        "<thead><tr><th>Indicateur</th><th>Valeur</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def render_status_table(status: Mapping[str, Any]) -> str:
    rows = [
        (label, status.get(key))
        for key, label in STATUS_FIELDS
        if key in status
    ]
    return render_value_table(rows, table_class="data status")


def _select(
    name: str,
    choices: Iterable[str],
    selected: Any = None,
    *,
    role: str = "",
) -> str:
    selected_text = str(selected or "")
    options: list[str] = []
    for choice in choices:
        selected_attr = (
            " selected" if str(choice) == selected_text else ""
        )
        options.append(
            f'<option value="{escape(str(choice))}"{selected_attr}>'
            f"{_cell(choice)}</option>"
        )
    return (
        f'<select name="{escape(name)}" data-role="{escape(role)}">'
        + "".join(options)
        + "</select>"
    )


def render_pending_table(questions: Iterable[Mapping[str, Any]]) -> str:
    items = list(questions)
    if not items:
        return '<p class="empty">Aucune question en attente. Tout est traité.</p>'
    headers = "".join(
        f"<th>{_cell(label)}</th>"
        for _, label in PENDING_FIELDS
    ) + (
        "<th>Catégorie</th><th>Sous-Catégorie</th>"
        "<th>Exercice</th><th>Source de paiement</th><th>Décision</th>"
    )
    body_rows: list[str] = []
    for index, question in enumerate(items, start=1):
        doc_id = escape(str(question.get("doc_id") or ""))
        selected_category = normalize_category(
            question.get("categorie") or question.get("category")
        ) or ""
        category_select = _select(
            f"categorie-{index}", CATEGORY_CHOICES, selected_category,
            role="category",
        )
        subcategory_select = _select(
            f"sous-categorie-{index}",
            ["", *SUBCATEGORY_CHOICES],
            question.get("sous_categorie") or "",
            role="subcategory",
        )
        exercise_select = _select(
            f"exercice-{index}",
            EXERCISE_CHOICES,
            question.get("exercise_year") or "2026",
            role="exercise",
        )
        source_default = question.get("source_paiement") or (
            "LCR"
            if str(question.get("source") or "").upper() == "BANQUE_OFX"
            else "BANQUE_PRO"
        )
        source_select = _select(
            f"source-{index}",
            PAYMENT_SOURCE_CHOICES,
            source_default,
            role="payment_source",
        )
        cells = "".join(
            f"<td>{_cell(question.get(key))}</td>"
            for key, _label in PENDING_FIELDS
        )
        decision_choices = (
            question.get("decision_choices") or DECISION_SIMPLE_CHOICES
        )
        decision_default = (
            question.get("decision_default") or "VALIDE"
        )
        decision_select = _select(
            f"decision-{index}",
            decision_choices,
            decision_default,
            role="decision",
        )
        body_rows.append(
            "<tr>"
            + cells
            + f"<td>{category_select}</td>"
            + f"<td>{subcategory_select}</td>"
            + f"<td>{exercise_select}</td>"
            + f"<td>{source_select}</td>"
            + f"<td>{decision_select} "
            f'<button type="button" '
            f'onclick="validerPiece(\'{doc_id}\', this)">Valider</button></td>'
            + "</tr>"
        )
    return (
        '<table class="data pending">'
        f"<thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )


def render_stage_table(report: Mapping[str, Any]) -> str:
    rows = [
        (label, report.get(key))
        for key, label in STAGE_FIELDS
        if key in report
    ]
    return render_value_table(rows, table_class="data stage")


def render_stage_details(report: Mapping[str, Any]) -> str:
    details = report.get("details") or ()
    if not details:
        return ""
    headers = "".join(
        f"<th>{_cell(key)}</th>"
        for key in details[0].keys()
    )
    body = "\n".join(
        "<tr>"
        + "".join(f"<td>{_cell(value)}</td>" for value in item.values())
        + "</tr>"
        for item in details
    )
    return (
        '<h4>Détail des pièces</h4>'
        '<table class="data details">'
        f"<thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>"
    )


def render_messages(report: Mapping[str, Any]) -> str:
    messages = report.get("messages") or ()
    if not messages:
        return ""
    items = "".join(f"<li>{_cell(message)}</li>" for message in messages)
    return f'<h4>Compte-rendu</h4><ul class="messages">{items}</ul>'


def load_pending_questions(
    *, tenant_id: str = "APD", root: Any = None
) -> list[dict[str, Any]]:
    """Load the full pending-question list from ``_A_COMPLETER.xlsx`` + SQLite.

    The cockpit must show the questions immediately, without waiting for the
    director to click the "Questions en attente" button.
    """
    try:
        from services.hermes_accounting_bridge import HermesAccountingBridge

        bridge = HermesAccountingBridge(tenant_id, root=root)
        return list(bridge.list_pending_questions())
    except Exception:  # noqa: BLE001
        return []


def dashboard_html(
    *,
    title: str = PAGE_TITLE,
    pending_questions: Iterable[Mapping[str, Any]] | None = None,
) -> str:
    """Return the complete French HTML dashboard (stages 1-4 as buttons)."""
    if pending_questions is None:
        pending_questions = load_pending_questions()
    pending_json = (
        json.dumps(list(pending_questions), ensure_ascii=True, default=str)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    payload = as_dict()
    payload["decision_simple"] = list(DECISION_SIMPLE_CHOICES)
    payload["decision_groups"] = list(DECISION_GROUPS)
    payload["decision_internal"] = list(DECISION_INTERNAL_CHOICES)
    payload["decision_group_targets"] = {
        key: {
            "counterparty": value[0],
            "category": value[1],
            "subcategory": value[2],
            "account": value[3],
        }
        for key, value in DECISION_GROUP_TARGETS.items()
    }
    payload["decision_internal_targets"] = {
        key: {
            "counterparty": value[0],
            "category": value[1],
            "subcategory": value[2],
            "account": value[3],
        }
        for key, value in INTERNAL_DECISION_TARGETS.items()
    }
    try:
        from services.tier_reference_service import TierReferenceService

        reference = TierReferenceService().bootstrap(write_missing=False)
        alias_targets = list(reference.alias_targets())
        payload["tier_alias_targets"] = alias_targets
        payload["decision_full"] = list(
            full_decision_choices(reference)
        )
    except Exception:  # noqa: BLE001
        alias_targets = [
            "CREER_NOUVEAU_FOURNISSEUR",
            "CREER_NOUVEAU_CLIENT",
            "RESTAURANTS_REPAS",
            "PEAGES_AUTOROUTES",
            "STATIONS_CARBURANT",
        ]
        payload["tier_alias_targets"] = alias_targets
        payload["decision_full"] = list(
            dict.fromkeys(
                (
                    *DECISION_SIMPLE_CHOICES,
                    *DECISION_GROUPS,
                    *DECISION_INTERNAL_CHOICES,
                    *alias_targets,
                )
            )
        )
    choices_json = (
        json.dumps(payload, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    buttons = "\n".join(
        f'<button type="button" class="stage" data-stage="{number}" '
        f'title="{escape(hint)}">{escape(label)}</button>'
        for number, label, hint in STAGE_LABELS
    )
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
:root{{--blue:#1f4e78;--ink:#172033;--muted:#5b6b7f;--line:#d8e0ea;--bg:#f7f9fc}}
*{{box-sizing:border-box}}
body{{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:var(--line);color:var(--ink)}}
header{{background:var(--blue);color:#fff;padding:18px 26px}}
h1{{font-size:20px;margin:0}}h2{{font-size:17px;margin:0 0 10px}}
main{{padding:22px 26px;display:grid;gap:18px}}
.card{{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:16px 18px}}
.actions{{display:flex;flex-wrap:wrap;gap:10px}}
button{{border:0;border-radius:7px;padding:10px 15px;font-size:14px;font-weight:600;cursor:pointer;background:var(--blue);color:#fff}}
button.secondary{{background:var(--muted)}}
button:disabled{{opacity:.55;cursor:wait}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}
th,td{{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}}
thead th{{background:var(--blue);color:#fff;font-weight:600}}
tbody tr:nth-child(even){{background:#f2f7fb}}
table.status th{{width:240px;background:#eef3f9;color:var(--ink)}}
ul.messages{{margin:6px 0 0 18px;padding:0;line-height:1.55}}
p.empty{{margin:0;color:var(--muted);font-style:italic}}
#state{{min-height:24px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}}
</style>
</head>
<body>
<header><h1>{escape(title)}</h1></header>
<main>
<section class="card">
<h2>Actions du dirigeant</h2>
<div class="actions">
{buttons}
<button type="button" class="secondary" id="status">Statut général</button>
<button type="button" class="secondary" id="pending">Questions en attente</button>
<button type="button" class="secondary" id="all">Tout exécuter</button>
<button type="button" class="secondary" id="apply-excel">Appliquer mes modifications Excel (_A_COMPLETER &amp; REFERENTIEL_TIERS)</button>
</div>
<p id="state">Prêt.</p>
</section>
<section class="card" id="summary"><h2>Statut général</h2><p class="empty">Cliquez sur « Statut général ».</p></section>
<section class="card" id="stage-result"><h2>Compte-rendu d'étape</h2><p class="empty">Lancez une étape 1 à 4.</p></section>
<section class="card" id="questions"><h2>Questions en attente</h2><p class="empty">Cliquez sur « Questions en attente ».</p></section>
</main>
<script>window.CHOICES = {choices_json};</script>
<script>window.INITIAL_PENDING = {pending_json};</script>
<script>
const state = document.getElementById('state');
function esc(value) {{
  if (value === null || value === undefined || value === '') return '—';
  const node = document.createElement('span');
  node.textContent = String(value);
  return node.innerHTML;
}}
function table(headers, rows) {{
  const head = headers.map(h => `<th>${{esc(h)}}</th>`).join('');
  const body = rows.map(row => '<tr>' + row.map(v => `<td>${{esc(v)}}</td>`).join('') + '</tr>').join('');
  return `<table><thead><tr>${{head}}</tr></thead><tbody>${{body}}</tbody></table>`;
}}
function kvRows(object, labels) {{
  return Object.keys(labels).filter(k => object && k in object).map(k => [labels[k], object[k]]);
}}
const STATUS_LABELS = {{tenant:'Dossier',year:'Exercice',pending_questions:'Questions en attente',pending:'Pièces en attente',validated:'Pièces validées',rejected:'Pièces rejetées',store_pending:'Écritures en attente',tenant_dir:'Répertoire de travail',export_dir:'Répertoire des classeurs',readable_workbook:'Classeur lisible'}};
const STAGE_LABELS = {{stage:'Étape',name:'Nom',status:'Statut',processed:'Pièces traitées',validated:'Pièces validées',pending:'Questions en attente',moved:'Fichiers déplacés',imported_ofx:'Lignes OFX importées',reconciled:'Rapprochements'}};
async function call(url) {{
  state.textContent = 'Exécution en cours...';
  try {{
    const response = await fetch(url, {{method: 'POST'}});
    const payload = await response.json();
    if (!response.ok) {{
      state.textContent = 'Erreur : ' + (payload.message || payload.error || response.status);
      return null;
    }}
    state.textContent = 'Terminé.';
    return payload;
  }} catch (error) {{
    state.textContent = 'Erreur réseau : ' + error;
    return null;
  }}
}}
async function showStatus() {{
  const data = await call('/api/status');
  if (!data) return;
  document.getElementById('summary').innerHTML =
    '<h2>Statut général</h2>' + table(['Indicateur','Valeur'], kvRows(data, STATUS_LABELS));
}}
function selectOptions(role, choices, selected) {{
  const options = choices.map(choice =>
    '<option value="' + esc(choice) + '"' +
    (String(choice) === String(selected || '') ? ' selected' : '') +
    '>' + esc(choice) + '</option>'
  ).join('');
  return '<select data-role="' + role + '">' + options + '</select>';
}}
async function showPending(initial) {{
  const data = initial || await call('/api/pending');
  if (!data) return;
  if (!data.length) {{
    document.getElementById('questions').innerHTML =
      '<h2>Questions en attente</h2>' +
      '<p class="empty">Aucune question en attente. Tout est traité.</p>';
    return;
  }}
  const rows = data.map(item => {{
    const doc = String(item.doc_id || '');
    const category = selectOptions('category', CHOICES.categorie, item.categorie);
    const subcategory = selectOptions('subcategory', ['', ...CHOICES.sous_categorie], item.sous_categorie || '');
    const exerciseDefault = item.exercise_year || '2026';
    const sourceDefault = item.source_paiement || (String(item.source || '').toUpperCase() === 'BANQUE_OFX' ? 'LCR' : 'BANQUE_PRO');
    const exercise = selectOptions('exercise', CHOICES.exercice_cible, exerciseDefault);
    const source = selectOptions('payment_source', CHOICES.source_paiement, sourceDefault);
    const decisionChoices = (item.decision_choices && item.decision_choices.length)
      ? item.decision_choices
      : (CHOICES.decision_simple || ['VALIDE', 'DEJA_PAYEE_2025', 'REPORTER_MOIS_SUIVANT', 'IMPAYEE']);
    const currentDecision = (item.decision && String(item.decision).toUpperCase() !== 'EN_ATTENTE')
      ? item.decision
      : ((item.decision_default || 'VALIDE'));
    const decision = selectOptions('decision', decisionChoices, currentDecision)
      .replace('<select ', '<select onchange="applyDecisionDefaults(this)" ');
    return '<tr data-doc-id="' + esc(doc) + '" data-decision-profile="' + esc(item.decision_profile || '') + '">' +
      '<td>' + esc(item.date_piece) + '</td>' +
      '<td>' + esc(item.source) + '</td>' +
      '<td>' + esc(item.fichier_ref || item.filename) + '</td>' +
      '<td>' + esc(item.fournisseur) + '</td>' +
      '<td>' + esc(item.libelle || item.numero_piece) + '</td>' +
      '<td>' + esc(item.montant_ttc) + '</td>' +
      '<td>' + esc(item.raison) + '</td>' +
      '<td>' + esc(item.action) + '</td>' +
      '<td>' + category + '</td>' +
      '<td>' + subcategory + '</td>' +
      '<td>' + exercise + '</td>' +
      '<td>' + source + '</td>' +
      '<td>' + decision + ' <button type="button" data-action="validate">Valider</button></td>' +
      '</tr>';
  }}).join('');
  document.getElementById('questions').innerHTML =
    '<h2>Questions en attente</h2>' +
    '<table class="data pending"><thead><tr>' +
    '<th>Date</th><th>Source</th><th>Fichier / Réf</th>' +
    '<th>Fournisseur / Tiers</th><th>Libellé / N° Pièce</th>' +
    '<th>Montant TTC (€)</th><th>Pourquoi en attente</th><th>Action attendue</th>' +
    '<th>Catégorie</th><th>Sous-Catégorie</th><th>Exercice Année</th>' +
    '<th>Source de paiement</th><th>Décision</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table>';
}}
function decisionTarget(choice) {{
  const token = String(choice || '').trim();
  if (!token) return '';
  if (token.indexOf('GROUPE:') === 0) return token;
  if ((CHOICES.decision_internal || []).indexOf(token) >= 0) return token;
  if ((CHOICES.tier_alias_targets || []).indexOf(token) >= 0) return token;
  return '';
}}
function applyDecisionDefaults(select) {{
  const mapping = (CHOICES.decision_group_targets || {{}})[select.value]
    || (CHOICES.decision_internal_targets || {{}})[select.value];
  if (!mapping) return;
  const row = select.closest('tr');
  if (!row) return;
  const category = row.querySelector('select[data-role="category"]');
  const subcategory = row.querySelector('select[data-role="subcategory"]');
  if (category && mapping.category) category.value = mapping.category;
  if (subcategory) subcategory.value = mapping.subcategory || '';
}}
async function validerPiece(docId, button) {{
  const row = button.closest('tr');
  const category = row.querySelector('select[data-role="category"]').value;
  const subcategory = row.querySelector('select[data-role="subcategory"]')
    ? row.querySelector('select[data-role="subcategory"]').value
    : '';
  const exercise = row.querySelector('select[data-role="exercise"]').value;
  const source = row.querySelector('select[data-role="payment_source"]').value;
  const decisionField = row.querySelector('select[data-role="decision"]');
  const decision = decisionField && decisionField.value ? decisionField.value : 'VALIDE';
  const response = await fetch('/api/decide', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      doc_id: docId,
      decision: decision || 'VALIDE',
      category: category,
      expense_subcategory: subcategory || null,
      fiscal_year_target: exercise,
      payment_source: source,
      tier_alias_target: decisionTarget(decision)
    }})
  }});
  let payload = {{}};
  try {{ payload = await response.json(); }} catch (error) {{}}
  if (!response.ok) {{
    state.textContent = 'Erreur lors de la validation : ' + (payload.message || payload.error || response.status);
    return;
  }}
  const status = String(payload.status || payload.decision || 'VALIDE').toUpperCase();
  if (status === 'EN_ATTENTE') {{
    if (decisionField) decisionField.value = decision;
    state.textContent = 'Décision enregistrée : la ligne reste à suivre.';
    return;
  }}
  row.remove();
  state.textContent = 'Pièce validée et enregistrée (' + status + ').';
}}
async function runStage(number) {{
  document.querySelectorAll('button.stage').forEach(b => b.disabled = true);
  const report = await call('/api/stage/' + number);
  document.querySelectorAll('button.stage').forEach(b => b.disabled = false);
  if (!report) return;
  let html = '<h2>Compte-rendu de l\\'étape ' + number + '</h2>' + table(['Indicateur','Valeur'], kvRows(report, STAGE_LABELS));
  if (report.messages && report.messages.length) {{
    html += '<h3>Messages</h3><ul>' + report.messages.map(m => `<li>${{esc(m)}}</li>`).join('') + '</ul>';
  }}
  if (report.details && report.details.length) {{
    const keys = Object.keys(report.details[0]);
    html += '<h3>Détail des pièces</h3>' + table(keys, report.details.map(item => keys.map(k => item[k])));
  }}
  document.getElementById('stage-result').innerHTML = html;
}}
async function runAll() {{
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  const reports = await call('/api/run-all');
  document.querySelectorAll('button').forEach(b => b.disabled = false);
  if (!Array.isArray(reports)) return;
  document.getElementById('stage-result').innerHTML =
    '<h2>Compte-rendu des 4 étapes</h2>' +
    table(['Étape','Nom','Statut','Pièces traitées','Validées','En attente','Rapprochements'],
      reports.map(r => [r.stage, r.name, r.status, r.processed, r.validated, r.pending, r.reconciled]));
}}
async function applyExcel() {{
  state.textContent = 'Exécution en cours... Application de vos modifications Excel.';
  try {{
    const response = await fetch('/api/apply-excel', {{method: 'POST'}});
    const payload = await response.json();
    if (!response.ok) {{
      state.textContent = 'Erreur : ' + (payload.message || payload.error || 'inconnue');
      return;
    }}
    const merged = (payload.aliases_merged || []).join(', ') || 'aucun';
    state.textContent =
      'Modifications appliquées. Alias fusionnés : ' + merged +
      ' ; décisions : ' + (payload.decisions_applied || 0) + '.';
    await showPending();
  }} catch (error) {{
    state.textContent = 'Erreur réseau : ' + error;
  }}
}}
document.querySelectorAll('button.stage').forEach(button => {{
  button.addEventListener('click', () => runStage(Number(button.dataset.stage)));
}});
document.getElementById('status').addEventListener('click', () => showStatus());
document.getElementById('pending').addEventListener('click', () => showPending());
document.getElementById('all').addEventListener('click', () => showAll());
document.getElementById('apply-excel').addEventListener('click', () => applyExcel());
document.addEventListener('click', (event) => {{
  const button = event.target.closest('button[data-action="validate"]');
  if (!button) return;
  const row = button.closest('tr');
  if (!row) return;
  validerPiece(row.dataset.docId || '', button);
}});
async function showAll() {{
  document.querySelectorAll('button.stage').forEach(b => b.disabled = true);
  await call('/api/run-all');
  document.querySelectorAll('button.stage').forEach(b => b.disabled = false);
  await Promise.all([showStatus(), showPending()]);
}}
if (window.INITIAL_PENDING && window.INITIAL_PENDING.length) {{
  showPending(window.INITIAL_PENDING);
}} else {{
  showPending();
}}
</script>
</body>
</html>"""


__all__ = [
    "PAGE_TITLE",
    "STAGE_LABELS",
    "dashboard_html",
    "load_pending_questions",
    "render_messages",
    "render_pending_table",
    "render_stage_details",
    "render_stage_table",
    "render_status_table",
    "render_value_table",
]
