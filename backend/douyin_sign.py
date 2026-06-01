"""
抖音 a_bogus 签名算法 — 纯 Python 实现
移植自 douyin-downloader-rust 项目的 src-tauri/src/sign/mod.rs
无需 JS 引擎或外部依赖
"""

import struct
import time
import urllib.parse

# ============================================================================
# RC4 加密
# ============================================================================

def rc4_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """RC4 流密码加密"""
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]

    i = 0
    j = 0
    cipher = bytearray()
    for byte in plaintext:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        t = (s[i] + s[j]) & 0xFF
        cipher.append(s[t] ^ byte)

    return bytes(cipher)


# ============================================================================
# SM3 哈希算法 (国密标准)
# ============================================================================

_IV = [
    0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
    0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
]

_TJ = [0x79CC4519] * 16 + [0x7A879D8A] * 48


def _rotate_left(x: int, n: int) -> int:
    """32 位循环左移"""
    n = n % 32
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _ff(x: int, y: int, z: int, j: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)


def _gg(x: int, y: int, z: int, j: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | ((~x & 0xFFFFFFFF) & z)


def _p0(x: int) -> int:
    return x ^ _rotate_left(x, 9) ^ _rotate_left(x, 17)


def _p1(x: int) -> int:
    return x ^ _rotate_left(x, 15) ^ _rotate_left(x, 23)


def sm3_hash(data: bytes) -> bytes:
    """计算 SM3 哈希值，返回 32 字节"""
    state = list(_IV)
    buf = bytearray(data)
    total_len = len(data)

    # 消息填充
    buf.append(0x80)
    while len(buf) % 64 != 56:
        buf.append(0)
    buf.extend(struct.pack('>Q', total_len * 8))

    # 分块压缩
    for offset in range(0, len(buf), 64):
        block = buf[offset:offset + 64]
        _sm3_compress(state, block)

    # 输出
    result = bytearray()
    for word in state:
        result.extend(struct.pack('>I', word))
    return bytes(result)


def _sm3_compress(state: list, block: bytes):
    """SM3 压缩函数"""
    w = [0] * 68
    w_prime = [0] * 64

    # 消息扩展
    for i in range(16):
        w[i] = struct.unpack('>I', block[i * 4:(i + 1) * 4])[0]

    for i in range(16, 68):
        w[i] = (_p1(w[i - 16] ^ w[i - 9] ^ _rotate_left(w[i - 3], 15))
                ^ _rotate_left(w[i - 13], 7)
                ^ w[i - 6]) & 0xFFFFFFFF

    for i in range(64):
        w_prime[i] = w[i] ^ w[i + 4]

    # 压缩
    a = list(state)

    for i in range(64):
        ss1 = _rotate_left(
            (_rotate_left(a[0], 12) + a[4] + _rotate_left(_TJ[i], i)) & 0xFFFFFFFF,
            7
        )
        ss2 = ss1 ^ _rotate_left(a[0], 12)
        tt1 = (_ff(a[0], a[1], a[2], i) + a[3] + ss2 + w_prime[i]) & 0xFFFFFFFF
        tt2 = (_gg(a[4], a[5], a[6], i) + a[7] + ss1 + w[i]) & 0xFFFFFFFF

        a[3] = a[2]
        a[2] = _rotate_left(a[1], 9)
        a[1] = a[0]
        a[0] = tt1
        a[7] = a[6]
        a[6] = _rotate_left(a[5], 19)
        a[5] = a[4]
        a[4] = _p0(tt2)

    for i in range(8):
        state[i] = (state[i] ^ a[i]) & 0xFFFFFFFF


# ============================================================================
# 自定义 Base64 编码
# ============================================================================

_S4 = b"Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe="
_S3 = b"ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe"


def _custom_base64_encode(data: bytes, table: bytes = _S4) -> str:
    """使用自定义字符表进行 Base64 编码"""
    result = []
    pad = len(data) % 3
    data_len = len(data) - pad

    for i in range(0, data_len, 3):
        n = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        result.append(chr(table[(n >> 18) & 0x3F]))
        result.append(chr(table[(n >> 12) & 0x3F]))
        result.append(chr(table[(n >> 6) & 0x3F]))
        result.append(chr(table[n & 0x3F]))

    if pad == 1:
        n = data[data_len] << 16
        result.append(chr(table[(n >> 18) & 0x3F]))
        result.append(chr(table[(n >> 12) & 0x3F]))
        result.append('=')
        result.append('=')
    elif pad == 2:
        n = (data[data_len] << 16) | (data[data_len + 1] << 8)
        result.append(chr(table[(n >> 18) & 0x3F]))
        result.append(chr(table[(n >> 12) & 0x3F]))
        result.append(chr(table[(n >> 6) & 0x3F]))
        result.append('=')

    return ''.join(result)


# ============================================================================
# 签名生成
# ============================================================================

_WINDOW_ENV_STR = "1536|747|1536|834|0|30|0|0|1536|834|1536|864|1525|747|24|24|Win32"


def _mix_random_byte(value: int, value_mask: int, salt: int, salt_mask: int) -> int:
    return ((value & value_mask) | (salt & salt_mask)) & 0xFF


def _generate_random_bytes() -> bytes:
    """生成随机字节"""
    now = time.time_ns()

    r1 = ((now & 0xFFFFFFFF) * 10000) % 10000
    r2 = (((now >> 32) & 0xFFFFFFFF) * 10000) % 10000
    r3 = (((now >> 16) & 0xFFFFFFFF) * 10000) % 10000

    result = bytearray()

    result.extend([
        _mix_random_byte(r1, 0xAA, 3, 0x55),
        _mix_random_byte(r1, 0x55, 3, 0xAA),
        _mix_random_byte(r1 >> 8, 0xAA, 45, 0x55),
        _mix_random_byte(r1 >> 8, 0x55, 45, 0xAA),
    ])

    result.extend([
        _mix_random_byte(r2, 0xAA, 1, 0x55),
        _mix_random_byte(r2, 0x55, 1, 0xAA),
        _mix_random_byte(r2 >> 8, 0xAA, 0, 0x55),
        _mix_random_byte(r2 >> 8, 0x55, 0, 0xAA),
    ])

    result.extend([
        _mix_random_byte(r3, 0xAA, 1, 0x55),
        _mix_random_byte(r3, 0x55, 1, 0xAA),
        _mix_random_byte(r3 >> 8, 0xAA, 5, 0x55),
        _mix_random_byte(r3 >> 8, 0x55, 5, 0xAA),
    ])

    return bytes(result)


def _generate_rc4_bb(params: str, user_agent: str, args: tuple) -> bytes:
    """生成 RC4 加密的中间数据"""
    start_time = int(time.time() * 1000)

    # SM3 哈希
    params_hash1 = sm3_hash(params.encode())
    params_hash2 = sm3_hash(params_hash1)

    cus_hash1 = sm3_hash(b"cus")
    cus_hash2 = sm3_hash(cus_hash1)

    # RC4 加密 UA
    ua_key = bytes([0, 1, args[2] & 0xFF])
    ua_encrypted = rc4_encrypt(user_agent.encode(), ua_key)

    # 用 S3 表编码 UA
    ua_encoded = _custom_base64_encode(ua_encrypted, _S3)
    ua_hash = sm3_hash(ua_encoded.encode())

    end_time = int(time.time() * 1000)

    # 构建 b 数组 (73 字节)
    b = bytearray(73)

    b[8] = 3

    # end_time 字节
    b[44] = (end_time >> 24) & 0xFF
    b[45] = (end_time >> 16) & 0xFF
    b[46] = (end_time >> 8) & 0xFF
    b[47] = end_time & 0xFF

    # start_time 字节
    b[20] = (start_time >> 24) & 0xFF
    b[21] = (start_time >> 16) & 0xFF
    b[22] = (start_time >> 8) & 0xFF
    b[23] = start_time & 0xFF

    # args 字节
    b[26] = (args[0] >> 24) & 0xFF
    b[27] = (args[0] >> 16) & 0xFF
    b[28] = (args[0] >> 8) & 0xFF
    b[29] = args[0] & 0xFF

    b[34] = (args[2] >> 24) & 0xFF
    b[35] = (args[2] >> 16) & 0xFF
    b[36] = (args[2] >> 8) & 0xFF
    b[37] = args[2] & 0xFF

    # 哈希值
    b[38] = params_hash2[21]
    b[39] = params_hash2[22]
    b[40] = cus_hash2[21]
    b[41] = cus_hash2[22]
    b[42] = ua_hash[23]
    b[43] = ua_hash[24]

    # 固定值
    b[18] = 44
    b[51] = (6241 >> 8) & 0xFF
    b[56] = 6383 & 0xFF
    b[57] = 6383 & 0xFF
    b[58] = (6383 >> 8) & 0xFF

    # window_env_str
    window_env_bytes = _WINDOW_ENV_STR.encode()
    b[64] = len(window_env_bytes) & 0xFF
    b[65] = len(window_env_bytes) & 0xFF

    # XOR 校验
    b[72] = (b[18] ^ b[20] ^ b[26] ^ b[30] ^ b[38] ^ b[40] ^ b[42]
             ^ b[21] ^ b[27] ^ b[31] ^ b[35] ^ b[39] ^ b[41] ^ b[43]
             ^ b[22] ^ b[28] ^ b[32] ^ b[36]
             ^ b[23] ^ b[29] ^ b[33] ^ b[37]
             ^ b[44] ^ b[45] ^ b[46] ^ b[47]
             ^ b[48] ^ b[49] ^ b[50]
             ^ b[24] ^ b[25]
             ^ b[52] ^ b[53] ^ b[54] ^ b[55]
             ^ b[57] ^ b[58] ^ b[59] ^ b[60]
             ^ b[65] ^ b[66]
             ^ b[70] ^ b[71])

    # 构建 bb 数组
    bb = bytearray()
    bb.append(b[18])
    bb.append(b[20])
    bb.extend(b[52:55])
    bb.extend(b[26:59])
    bb.extend(b[38:44])
    bb.extend(b[21:23])
    bb.extend(b[27:38])
    bb.extend(b[44:61])
    bb.extend(b[24:26])
    bb.extend(b[65:67])
    bb.extend(b[70:72])
    bb.extend(window_env_bytes)
    bb.append(b[72])

    # RC4 加密
    return rc4_encrypt(bytes(bb), bytes([121]))


def sign(params: str, user_agent: str, args: tuple = (0, 1, 14)) -> str:
    """生成 a_bogus 签名"""
    random_bytes = _generate_random_bytes()
    rc4_data = _generate_rc4_bb(params, user_agent, args)

    combined = random_bytes + rc4_data
    return _custom_base64_encode(combined) + "="


def sign_detail(params: str, user_agent: str) -> str:
    """详情接口签名 (args = [0, 1, 14])"""
    return sign(params, user_agent, (0, 1, 14))


def sign_reply(params: str, user_agent: str) -> str:
    """评论接口签名 (args = [0, 1, 8])"""
    return sign(params, user_agent, (0, 1, 8))


def sign_params(params: dict, user_agent: str, is_reply: bool = False) -> str:
    """
    便捷函数：对参数字典进行签名
    返回 a_bogus 值
    """
    params_str = urllib.parse.urlencode(params)
    if is_reply:
        return sign_reply(params_str, user_agent)
    return sign_detail(params_str, user_agent)
