from reporting.routes import model_profiles


async def test_list_model_profiles_includes_global_run_cost_cap(mocker):
    mocker.patch.object(model_profiles.report_store, "list_model_profiles", return_value=[])
    mocker.patch.object(model_profiles.settings, "CHAT_RUN_COST_BUDGET_USD", 1.25)

    result = await model_profiles.list_model_profiles(current=mocker.Mock())

    assert result.profiles == []
    assert result.global_run_cost_budget_usd == 1.25
