from __future__ import annotations

from flask import Flask, jsonify, render_template_string, request

from graph import _build_id_to_info, _find_node_id_key, _node_id, _node_label, build_graph
from schema import load_schema
from state import _mark_dirty, get_working_ontology, is_dirty, save_to_disk
from template import HTML_TEMPLATE

app = Flask(__name__)


def _safe_ontology_name() -> str:
    try:
        ont = get_working_ontology()
        return str(ont.get("metadata", {}).get("ontology_name", "Ontology graph"))
    except Exception:
        return "Ontology graph"


@app.route("/")
def index() -> str:
    return render_template_string(HTML_TEMPLATE, ontology_name=_safe_ontology_name())


@app.route("/data")
def data_endpoint():
    ont = get_working_ontology()
    return jsonify(build_graph(ont))


@app.route("/schema")
def schema_endpoint():
    return jsonify(load_schema(get_working_ontology()))


@app.route("/node/<node_id>")
def node_detail(node_id: str):
    ont = get_working_ontology()
    id_info = _build_id_to_info(ont)

    found_obj = None
    found_type = None
    for ntype, items in ont.get("nodes", {}).items():
        for obj in items:
            if _node_id(obj) == node_id:
                found_obj = obj
                found_type = ntype
                break
        if found_obj:
            break

    if not found_obj:
        return jsonify({"error": "Node not found"}), 404

    schema = load_schema(ont)
    schema_props = schema.get("node_types", {}).get(found_type, [])
    merged_attrs = {}
    for prop in schema_props:
        merged_attrs[prop["name"]] = found_obj.get(prop["name"], "")
    for k, v in found_obj.items():
        if k not in merged_attrs:
            merged_attrs[k] = v
    for k, v in merged_attrs.items():
        if k not in found_obj:
            found_obj[k] = v

    rels_out = []
    rels_in = []
    for idx, rel in enumerate(ont.get("relationships", [])):
        if str(rel.get("from_id", "")) == node_id:
            to_id = str(rel.get("to_id", ""))
            info = id_info.get(to_id, {"type": "?", "label": to_id})
            rels_out.append({
                "index": idx,
                "type": rel.get("type", ""),
                "to_id": to_id,
                "to_label": info["label"],
                "to_type": info["type"],
            })
        elif str(rel.get("to_id", "")) == node_id:
            from_id = str(rel.get("from_id", ""))
            info = id_info.get(from_id, {"type": "?", "label": from_id})
            rels_in.append({
                "index": idx,
                "type": rel.get("type", ""),
                "from_id": from_id,
                "from_label": info["label"],
                "from_type": info["type"],
            })

    return jsonify({
        "id": node_id,
        "type": found_type,
        "attributes": merged_attrs,
        "relationships_out": rels_out,
        "relationships_in": rels_in,
    })


@app.route("/node/<node_id>/update", methods=["POST"])
def node_update(node_id: str):
    ont = get_working_ontology()
    body = request.get_json(force=True)
    new_attrs = body.get("attributes", {})

    for ntype, items in ont.get("nodes", {}).items():
        for obj in items:
            if _node_id(obj) == node_id:
                id_key = _find_node_id_key(obj)
                for k, v in new_attrs.items():
                    if k == id_key:
                        continue
                    obj[k] = v
                _mark_dirty()
                label = _node_label(obj, node_id)
                return jsonify({
                    "ok": True,
                    "vis_node": {
                        "id": node_id,
                        "label": label,
                        "group": ntype,
                        "title": f"{ntype}: {label}<br><code>{node_id}</code>",
                    },
                })

    return jsonify({"error": "Node not found"}), 404


@app.route("/node/<node_id>/delete", methods=["POST"])
def node_delete(node_id: str):
    ont = get_working_ontology()

    removed = False
    for ntype, items in ont.get("nodes", {}).items():
        for i, obj in enumerate(items):
            if _node_id(obj) == node_id:
                items.pop(i)
                removed = True
                break
        if removed:
            break

    if not removed:
        return jsonify({"error": "Node not found"}), 404

    rels = ont.get("relationships", [])
    ont["relationships"] = [
        r for r in rels
        if str(r.get("from_id", "")) != node_id and str(r.get("to_id", "")) != node_id
    ]
    removed_rels = len(rels) - len(ont["relationships"])

    _mark_dirty()
    return jsonify({"ok": True, "removed_relationships": removed_rels})


@app.route("/relationship/add", methods=["POST"])
def relationship_add():
    ont = get_working_ontology()
    body = request.get_json(force=True)
    rtype = body.get("type", "")
    from_id = body.get("from_id", "")
    to_id = body.get("to_id", "")

    if not rtype or not from_id or not to_id:
        return jsonify({"error": "type, from_id, to_id required"}), 400

    ont.setdefault("relationships", []).append({
        "type": rtype,
        "from_id": from_id,
        "to_id": to_id,
    })
    _mark_dirty()

    idx = len(ont["relationships"]) - 1
    return jsonify({
        "ok": True,
        "edge": {
            "id": f"e{idx}",
            "from": from_id,
            "to": to_id,
            "label": rtype,
            "title": rtype,
            "arrows": "to",
        },
    })


@app.route("/relationship/<int:index>/delete", methods=["POST"])
def relationship_delete(index: int):
    ont = get_working_ontology()
    rels = ont.get("relationships", [])
    if index < 0 or index >= len(rels):
        return jsonify({"error": "Invalid index"}), 400

    removed = rels.pop(index)
    _mark_dirty()
    return jsonify({"ok": True, "removed": removed})


@app.route("/save", methods=["POST"])
def save_endpoint():
    result = save_to_disk()
    return jsonify(result)


@app.route("/status")
def status_endpoint():
    ont = get_working_ontology()
    return jsonify({
        "has_unsaved_changes": is_dirty(),
        "version": ont.get("metadata", {}).get("version", "?"),
    })


@app.route("/all_nodes")
def all_nodes_endpoint():
    ont = get_working_ontology()
    result = []
    for ntype, items in ont.get("nodes", {}).items():
        for obj in items:
            nid = _node_id(obj)
            if nid:
                result.append({"id": nid, "label": _node_label(obj, nid), "type": ntype})
    return jsonify(result)
