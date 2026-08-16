"""
账号池 API 蓝图
风格对齐 backend/proxy.py（蓝图 + jsonify）
"""

from flask import Blueprint, jsonify, request

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


@account_pool_bp.route("/<account_id>", methods=["PUT"])
def update_account(account_id):
    """更新账号备注名、别名等"""
    data = request.get_json() or {}
    updated = account_pool.update_account_info(account_id, data)
    if not updated:
        return jsonify({"error": "未找到该账号"}), 404
    return jsonify({"message": "更新成功", "account": updated})


@account_pool_bp.route("/<account_id>/verify", methods=["POST"])
def verify_single_account(account_id):
    """对单个账号执行真实接口探活与状态刷新"""
    res = account_pool.verify_account(account_id)
    if res.get("status") == "not_found":
        return jsonify({"error": "未找到该账号"}), 404
    return jsonify(res)


@account_pool_bp.route("/verify-all", methods=["POST"])
def verify_all_accounts():
    """批量检测所有账号并返回结果"""
    results = account_pool.verify_all()
    return jsonify({"results": results, "summary": account_pool.get_summary()})


@account_pool_bp.route("/<account_id>/revive", methods=["POST"])
def revive_account(account_id):
    """重新激活异常或被踢出的账号"""
    ok = account_pool.revive(account_id)
    if not ok:
        return jsonify({"error": "未找到该账号"}), 404
    # 激活后自动探活一次更新状态
    res = account_pool.verify_account(account_id)
    return jsonify({"message": "已重新激活并验证", "verify_result": res})


@account_pool_bp.route("/events", methods=["GET"])
def get_events():
    """取走踢出事件队列"""
    events = account_pool.pop_kick_events()
    return jsonify({"events": events})
