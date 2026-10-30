# oracle2excel — 上游 Oracle 数据导出 Excel 服务

从上游 **Oracle 11g** 数据库批量导出数据到 `.xlsx` 文件的容器化服务。

## 技术要点

| 项目 | 说明 |
|---|---|
| 驱动 | `python-oracledb` **Thick 模式**（11g 必选，见下方说明） |
| Instant Client | **Oracle Instant Client 19.25**（Basic 包，打包进镜像） |
| 连接方式 | `host:port/service_name`（`oracledb.makedsn`，11g 推荐） |
| Excel 引擎 | `openpyxl` write_only 流式写出，内存占用恒定，自动分 Sheet |
| 运行环境 | Docker / Docker Compose，非 root 用户运行 |

> ⚠️ **为什么用 Thick 模式**：python-oracledb 的 Thin 模式**只支持 Oracle Database 12.1+**，
> 官方文档明确 Thin 模式无法连接 11g。连接 11.2 必须加载 **Instant Client 19**（Thick 模式）——
> 这正是镜像中打包 IC19 + `ORACLE_HOME`/`LD_LIBRARY_PATH` + `libaio1` 的原因。
> （注意：这里的 Thick/Thin 指 python-oracledb 驱动模式，与 JDBC 的 thin URL 无关。）

## 目录结构

```
oracle2excel/
├── app/                  # Python 源码
│   ├── main.py           # 入口（once/loop 两种运行模式）
│   ├── config.py         # oracle_conn.json 加载与校验（含防注入）
│   ├── db.py             # Thick 初始化 / service_name DSN / 重试 / LOB 类型处理
│   ├── exporter.py       # 流式导出核心（分 Sheet、超长截断、原子落盘）
│   └── logger.py
├── config/
│   └── oracle_conn.json  # 连接与导出配置（部署时修改）
├── output/               # 导出的 xlsx 落盘目录（compose 挂载）
├── tests/test_export.py  # 无数据库的 mock 端到端自检
├── Dockerfile            # python:3.11-slim-bookworm + IC19 + libaio1
├── docker-compose.yml
├── build.sh              # 一键: 构建镜像（构建期下载IC） → 验证 → save 打包 TGZ
└── requirements.txt      # python-oracledb==2.4.3, openpyxl==3.1.5
```

## 构建镜像并打包 TGZ

```bash
./build.sh            # 默认版本 1.0.0
./build.sh 1.2.0      # 自定义版本号
```

脚本会自动：
1. `docker build` 构建镜像（Dockerfile 在构建阶段在线下载 Instant Client 19.25，约 75MB；国内网络慢时可用 `--build-arg INSTANTCLIENT_URL=<镜像地址>` 或 `INSTANTCLIENT_URL=<镜像地址> ./build.sh` 指定其他下载源）;
2. 容器内验证 Thick 模式能否真正加载 `libclntsh.so.19.1`;
3. `docker save | gzip` 生成 `dist/oracle2excel_<版本>_linux_amd64.tar.gz` 并输出 SHA256。

## 部署（目标机器）

```bash
docker load -i oracle2excel_1.0.0_linux_amd64.tar.gz

# 1. 修改配置
vim config/oracle_conn.json

# 2. 准备输出目录（容器内以 uid=10001 运行）
mkdir -p output && sudo chown -R 10001:10001 output

# 3. 运行
docker compose up            # once 模式前台跑完退出
docker compose run --rm oracle2excel   # 等价
docker logs -f oracle2excel  # 查看日志
```

若配置为 `run_mode: "loop"`，把 `docker-compose.yml` 中 `restart` 改为 `unless-stopped`。

## 配置说明（config/oracle_conn.json）

```jsonc
{
  "oracle": {
    "user": "...", "password": "...",
    "host": "192.168.1.100", "port": 1521,
    "service_name": "orcl",          // 11g 务必用 service_name
    "driver_mode": "thick",          // 11g 必须 thick；thin 仅 12.1+
    "connect_retry_attempts": 3
  },
  "run_mode": "once",                // once=导完退出; loop=周期导出
  "tables": ["T_ORDER", "T_USER_INFO"],            // 整表导出
  "queries": [                                     // 自定义 SQL 导出
    {"name": "order_daily", "sql": "SELECT ... WHERE CREATED_AT >= TRUNC(SYSDATE)-1"}
  ],
  "export_all_user_tables": false,   // true: 自动导出当前用户全部表
  "excel": { "max_rows_per_sheet": 1000000 }       // 超过自动拆 DATA_1/DATA_2...
}
```

## 功能与防护

- **LOB 安全**：CLOB/NCLOB/BLOB 经输出类型处理器转为字符串/字节，BLOB 以 hex 截断写入，不会拿到 LOB locator 或撑爆内存;
- **Excel 限制兜底**：单元格 32767 字符截断、单 Sheet 1048576 行自动分 Sheet、NaN/Infinity 归一为空;
- **SQL 注入防护**：表名白名单校验，自定义 SQL 强制 `SELECT` 开头;
- **原子落盘**：先写 `.tmp` 再 rename，中断不产生半成品文件; 单任务失败不影响其余任务;
- **退出码**：0 成功 / 2 配置错误 / 3 连接失败 / 4 导出失败，方便上游调度判断。

## 代码自检（无数据库时）

```bash
.venv/bin/python tests/test_export.py   # mock 驱动端到端验证，38 项断言
```
