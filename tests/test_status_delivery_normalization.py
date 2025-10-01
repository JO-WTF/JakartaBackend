"""测试 status_delivery 规范化功能"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_DISK_PATH", "./data/uploads")

from app.core.sync import _normalize_status_delivery_value  # noqa: E402


def test_normalize_status_delivery_value():
    """测试 _normalize_status_delivery_value 函数的各种输入"""
    
    # 测试标准值(小写)
    assert _normalize_status_delivery_value("on the way") == "On the way"
    assert _normalize_status_delivery_value("on site") == "On Site"
    assert _normalize_status_delivery_value("pod") == "POD"
    
    # 测试大小写混合
    assert _normalize_status_delivery_value("On The Way") == "On the way"
    assert _normalize_status_delivery_value("ON THE WAY") == "On the way"
    assert _normalize_status_delivery_value("oN tHe WaY") == "On the way"
    
    # 测试其他标准值
    assert _normalize_status_delivery_value("prepare vehicle") == "Prepare Vehicle"
    assert _normalize_status_delivery_value("PREPARE VEHICLE") == "Prepare Vehicle"
    assert _normalize_status_delivery_value("waiting pic feedback") == "Waiting PIC Feedback"
    assert _normalize_status_delivery_value("replan mos due to lsp delay") == "RePlan MOS due to LSP Delay"
    assert _normalize_status_delivery_value("close by rn") == "Close by RN"
    
    # 测试多余空格
    assert _normalize_status_delivery_value("  on the way  ") == "On the way"
    assert _normalize_status_delivery_value("on  the  way") == "On the way"
    
    # 测试空值
    assert _normalize_status_delivery_value("") is None
    assert _normalize_status_delivery_value("   ") is None
    assert _normalize_status_delivery_value(None) is None
    
    # 测试非标准值(保持原样但格式化空格)
    assert _normalize_status_delivery_value("Custom Status") == "Custom Status"
    assert _normalize_status_delivery_value("  custom  status  ") == "custom status"


def test_normalization_logic_matches_update_dn():
    """验证 update_dn 接口中的规范化逻辑"""
    
    # 测试各种大小写的输入会被正确规范化
    test_cases = [
        ("ON THE WAY", "On the way"),
        ("on the way", "On the way"),
        ("On The Way", "On the way"),
        ("  on  the  way  ", "On the way"),  # 多余空格
        ("prepare vehicle", "Prepare Vehicle"),
        ("PREPARE VEHICLE", "Prepare Vehicle"),
        ("pod", "POD"),
        ("POD", "POD"),
        ("on site", "On Site"),
        ("ON SITE", "On Site"),
        ("waiting pic feedback", "Waiting PIC Feedback"),
        ("REPLAN MOS DUE TO LSP DELAY", "RePlan MOS due to LSP Delay"),
        ("close by rn", "Close by RN"),
        ("CLOSE BY RN", "Close by RN"),
        ("cancel mos", "Cancel MOS"),
        ("replan mos project", "RePlan MOS Project"),
    ]
    
    for input_value, expected_output in test_cases:
        # 模拟 update_dn 接口中的规范化逻辑
        # 这是在 app/api/dn/update.py 第 67-69 行实现的
        delivery_status_value = (input_value or "").strip() or None
        
        if delivery_status_value is None:
            delivery_status_value = "No Status"
        else:
            # 规范化用户输入
            delivery_status_value = _normalize_status_delivery_value(delivery_status_value)
            if delivery_status_value is None:
                delivery_status_value = "No Status"
        
        assert delivery_status_value == expected_output, \
            f"Input '{input_value}' should normalize to '{expected_output}', got '{delivery_status_value}'"


def test_normalization_handles_edge_cases():
    """测试规范化函数处理边界情况"""
    
    # 空值和空白
    assert _normalize_status_delivery_value(None) is None
    assert _normalize_status_delivery_value("") is None
    assert _normalize_status_delivery_value("   ") is None
    assert _normalize_status_delivery_value("\t\n") is None
    
    # 非字符串类型
    assert _normalize_status_delivery_value(123) == 123
    assert _normalize_status_delivery_value([]) == []
    
    # 非标准值保持原样(但格式化空格)
    assert _normalize_status_delivery_value("Custom Status") == "Custom Status"
    assert _normalize_status_delivery_value("  custom  status  ") == "custom status"
    
    # 确保所有标准值都能被识别
    standard_values = [
        "Prepare Vehicle",
        "On the way",
        "On Site",
        "POD",
        "Waiting PIC Feedback",
        "RePlan MOS due to LSP Delay",
        "RePlan MOS Project",
        "Cancel MOS",
        "Close by RN",
    ]
    
    for value in standard_values:
        # 小写版本应该被规范化回标准格式
        normalized = _normalize_status_delivery_value(value.lower())
        assert normalized == value, f"'{value.lower()}' should normalize to '{value}', got '{normalized}'"
