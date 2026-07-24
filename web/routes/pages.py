from flask import Blueprint, render_template

bp = Blueprint("pages", __name__)


@bp.route("/")
def page_jobs():
    return render_template("index.html", page="jobs")


@bp.route("/config")
def page_config():
    return render_template("settings.html", page="config")


@bp.route("/history")
def page_history():
    return render_template("history.html", page="history")


@bp.route("/stats")
def page_stats():
    return render_template("stats.html", page="stats")


@bp.route("/status")
def page_status():
    return render_template("status.html", page="status")


@bp.route("/rss")
def page_rss():
    return render_template("rss.html", page="rss")


@bp.route("/inventory")
def page_inventory():
    return render_template("inventory.html", page="inventory")
