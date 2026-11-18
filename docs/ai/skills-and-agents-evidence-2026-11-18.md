# Применение навыков и ролей — свидетельства

18 ноября 2026. Наличие конфигурации не считается выполненной работой. Ниже приведены реально полученные артефакты и проверки.

| Роль/скилл | Фактическая работа | Свидетельство |
|---|---|---|
| Analyst / analyst-requirements | Сверкаполногообъёма,реальнаяпередача5профильнымролям | full-ai-ds-task-plan-2026-11-17.md,A01–A29 |
| Data / data-poi-quality-check | Аудит2058реальныхPOI,повторнаянормализация/провенанс/покрытие | scripts/audit_geoapify_readiness.py,reports/data/geoapify-readiness.2026-11-17.json |
| Recommendation / recommendation-evaluation | 30сценариев/204техническиепроверки,20unitпослеявныхwarnings | scripts/evaluate_recommendation_scenarios.py,tests/test_recommendation.py |
| Architect | Точныйopt-in0.2context/matrix/sidecars/бюджет,сохранение0.1 | ai_ds/routing_v02_contract.py,routing-contract-v0.2.md,18tests |
| Routing / route-validation | Multiday/MRV/темп/паузы/required/unknown/Assistant | multiday.py,route_assistant.py,25+24tests,2localCLIварианта |
| Независимыйqa_current_v02 / qa-integration-check,route-validation | Нашёл4false-validprobes,повторно67targettests;отдельно7durationtests | qa-v02-2026-11-18.md,reports/qa/multiday-validator-fix.2026-11-18.json |
| Основнойагент | Фактическаясборка/запуск,первыйcapdefect,14OpenRouter/8GeoapifyRouting/4CLItests,liveпроверки | reports/llm, reports/routing, reports/qa |

Childwritesожидалисистемногоapproval и не исполнялись; исходники были переданы основномуагенту сообщениями и сохранены им. Этоограничениеинструмента не подменяет проверку фактических файлов/тестов. Backend/UIчужогоучастниканеизменялись.

Настоящийстороннийвызов: mcp__codex_apps__github_get_repo({repository_full_name:weiffii/TripWise}) и mcp__codex_apps__github_search_branches({owner:weiffii,repo_name:TripWise,query:feature/ai-ds,page_size:10}),17ноября2026. Реальныйответподтвердилрепозиторий/ветку. Санитизированныйselectedstructuredresult: reports/tools/github-mcp.2026-11-17.json. Этоread-onlyMCP,нефейковыйлокальныйmockинеGitpush.

OpenRouter/Geoapify HTTPвызовы отдельныотMCP: ониподтверждаютреальнуюLLM/маршрутнуюинтеграцию,ноневыдаютсязаMCP. Синтетическиетестыневыдавалисьзаотзывытуристов. Реальныеслайды/вебдемо/пользовательскиетестыостаютсяотдельнымидоказательствами.
