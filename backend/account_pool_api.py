"""
账号池 API 蓝图
风格对齐 backend/proxy.py（蓝图 + jsonify）
"""

from flask import Blueprint, jsonify

from backend.account_pool import account_pool

account_pool_bp = Blueprint("account_pool", __name__, url_prefix="/api/account-pool")


@account_pool_bp.route("", methods=["GET"])
def list_accounts():
    """列出所有账号（脱敏）"""
    accounts = account_pool.list_accounts()
    return jsonify({"accounts": accounts, "total": len(accounts)})


@account_pool_bp.route("/summary", methods=["GET"])
def get_summary():
    """概要统计"""
    return jsonify(account_pool.get_summary())


@account_pool_bp.route("/<account_id>", methods=["DELETE"])
def remove_account(account_id):
    """从池中删除账号"""
    removed = account_pool.remove(account_id)
    if not removed:
        return jsonify({"error": "未找到该账号"}), 404
    return jsonify({"message": "已删除"})


@account_pool_bp.route("/events", methods=["GET"])
def get_events():
    """取走踢出事件队列"""
    events = account_pool.pop_kick_events()
    return jsonify({"events": events})
