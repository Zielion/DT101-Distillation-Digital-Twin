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


def test_ai_fallback_explains_automatic_process_alarm_needs_no_intervention():
    assistant = AIAssistant(api_key=None)

    response = assistant.recommend(
        alarm_context={
            "active_alarms": ["DT101.ALARM.FEED_TANK_HIGH_HIGH"],
            "mode": "NORMAL_OPERATION",
        },
        recent_history=[],
    )

    assert "Feed tank" in response
    assert "automatic" in response
    assert "No operator intervention is necessary" in response
    assert "resolve itself" in response


def test_ai_fallback_gives_manual_recovery_steps_for_capacity_alarm_that_locks_equipment():
    assistant = AIAssistant(api_key=None)

    response = assistant.recommend(
        alarm_context={
            "active_alarms": ["DT101.ALARM.FEED_TANK_OVERFILL"],
            "mode": "NORMAL_OPERATION",
        },
        recent_history=[],
    )

    assert "90%" in response
    assert "manually open V-100" in response
    assert "distillate product" in response
    assert "bottom product" in response
    assert "resume automatic operation" in response


def test_ai_fallback_treats_high_high_pressure_as_interlock_protected_alarm():
    assistant = AIAssistant(api_key=None)

    response = assistant.recommend(
        alarm_context={
            "active_alarms": ["DT101.ALARM.HIGH_HIGH_PRESSURE"],
            "mode": "SHUTDOWN",
        },
        recent_history=[],
    )

    assert "pressure" in response
    assert "Do not bypass the pressure interlock" in response
    assert "condenser cooling" in response
    assert "normal automatic sequence" in response
