# 多人共享记账系统

## 安装

需要 Python 3.12、uv 和 make。在仓库根目录执行以下命令，按锁定版本安装依赖。

```sh
cd lab3/code
make install
```

## 启动

在两个终端中分别进入 `lab3/code`。先在第一个终端启动服务并保持运行。

```sh
make run-server
```

在第二个终端打开桌面客户端。

```sh
make run-client
```

首次使用时，在桌面窗口注册账号，登录后创建账本。两个客户端可以连接同一个后端。
默认服务地址为 `http://127.0.0.1:8000`，数据库保存在 `data/ledger.sqlite3`。
结束使用时，关闭桌面窗口，在服务端终端按 Ctrl+C。

## 测试与构建

在 `lab3/code` 中按需执行。

```sh
make test         # 运行自动化测试
make check        # 运行测试、代码风格、服务联通和桌面操作检查
make performance  # 运行一万条收支记录的并发查询测试
make build        # 在 dist/ 生成 wheel 和源码包
```
