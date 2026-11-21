UV ?= uv

.PHONY: help install build init-db run-server run-client test lint smoke api-smoke desktop-smoke
.PHONY: entry-smoke change-smoke report-smoke performance check

help:
	@echo "install     安装锁定版本的依赖，创建 .venv"
	@echo "build       构建 wheel 和源码包，输出到 dist/"
	@echo "init-db     建立或检查数据库结构，不插入演示数据"
	@echo "run-server  启动后端服务（独立终端）"
	@echo "run-client  启动桌面界面（独立终端）"
	@echo "test        运行测试"
	@echo "lint        检查代码风格"
	@echo "smoke       检查实际后端和桌面窗口联通并自动退出"
	@echo "api-smoke   用临时账号验证真实 HTTP 登录及账本流程"
	@echo "desktop-smoke   用两个桌面窗口检查账号、邀请和成员管理"
	@echo "entry-smoke     用两个桌面窗口检查收支登记、审核和汇总"
	@echo "change-smoke    用两个桌面窗口检查凭证、更正和作废"
	@echo "report-smoke     验证桌面查询、退款、结清、导出、退出及日志"
	@echo "performance      20 个账号在同一万笔账本执行 100 次 HTTP 查询"
	@echo "check       执行测试、风格、启动和桌面操作检查"

install:
	$(UV) sync --locked

build:
	$(UV) run --locked python -m build --no-isolation

init-db:
	$(UV) run --locked python scripts/init_db.py

run-server:
	$(UV) run --locked ledger-server

run-client:
	$(UV) run --locked ledger-desktop

test:
	$(UV) run --locked pytest

lint:
	$(UV) run --locked pylint app scripts

smoke:
	$(UV) run --locked python scripts/smoke_check.py

api-smoke:
	$(UV) run --locked python scripts/api_smoke.py

desktop-smoke:
	$(UV) run --locked python scripts/desktop_smoke.py

entry-smoke:
	$(UV) run --locked python scripts/desktop_entry_smoke.py

change-smoke:
	$(UV) run --locked python scripts/desktop_change_smoke.py

report-smoke:
	$(UV) run --locked python scripts/desktop_report_smoke.py

performance:
	$(UV) run --locked python scripts/query_acceptance.py

check:
	$(MAKE) test
	$(MAKE) lint
	$(MAKE) smoke
	$(MAKE) api-smoke
	$(MAKE) desktop-smoke
	$(MAKE) entry-smoke
	$(MAKE) change-smoke
	$(MAKE) report-smoke
