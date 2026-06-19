from distillation.ai_assistant import AIAssistant


def test_ai_prompt_contains_alarm_evidence_and_safety_rules():
    assistant = AIAssistant(api_key=None)

    prompt = assistant.build_prompt(
        alarm_context={
            "mode": "FAULT_HANDLING",
            "active_alarms": ["DT101.ALARM.REFLUX_VALVE_STUCK"],
        },
        recent_history=[
            {"tag": "DT101.CMD.REFLUX_VALVE", "value": 70.0},
            {"tag": "DT101.FB.REFLUX_VALVE_POSITION", "value": 20.0},
        ],
    )

    assert "DT101.ALARM.REFLUX_VALVE_STUCK" in prompt
    assert "DT101.CMD.REFLUX_VALVE" in prompt
    assert "never recommend bypassing high-pressure interlock" in prompt
    assert "do not directly control actuators" in prompt


def test_ai_fallback_returns_structured_recommendation_without_api_key():
    assistant = AIAssistant(api_key=None)

    response = assistant.recommend(
        alarm_context={"active_alarms": ["DT101.ALARM.DATA_STALE"], "mode": "FAULT_HANDLING"},
        recent_history=[],
    )

    assert "Fault summary:" in response
    assert "Evidence:" in response
    assert "Safety caution:" in response
