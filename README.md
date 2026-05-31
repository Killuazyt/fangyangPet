# GPU Rowlet Monitor

独立 Windows 桌宠，复用 `D:\Project\Pet\rowlet\spritesheet.webp`，通过 SSH 查询 Ubuntu 服务器的 NVIDIA GPU 状态。只要任意一张卡空闲，Rowlet 会切到配置的打瞌睡状态并弹出气泡说明哪些 GPU 空闲。

## 安装

```powershell
cd D:\Project\Pet\gpu-rowlet-monitor
python -m pip install -r requirements.txt
```

直接启动也可以，Rowlet 会先正常出现；右键 Rowlet 选择“设置”，填写服务器 IP、端口、账号，并选择 SSH 密钥或账号密码。

`config.json` 只保存服务器地址、端口、用户名、认证方式和 SSH 私钥路径。密码不会写入配置文件，会通过 `keyring` 存到 Windows 凭据库。`config.json` 已加入 `.gitignore`。

## SSH 密钥

生成或检查密钥：

```powershell
.\setup_ssh.ps1
```

把输出的公钥加入 Ubuntu 用户的 `~/.ssh/authorized_keys`。首次连接建议手动测试并确认服务器指纹：

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519" -p 22 ubuntu@192.168.1.100 "nvidia-smi -L"
```

## 运行

测试 SSH 和 `nvidia-smi`：

```powershell
.\run.ps1 -TestSsh
```

只查询一次并输出 JSON：

```powershell
.\run.ps1 -Once
```

启动桌宠：

```powershell
.\run.ps1
```

右键菜单：

- `设置`：填写 IP、端口、账号、SSH 私钥路径或密码。
- `查看状态`：打开状态窗口，居中显示每张 GPU 的利用率、显存占用、空闲判断和占卡进程；选中进程后可在下方查看完整命令。
- `立即查询`：马上查询一次 GPU 状态并更新宠物状态。
- `退出`：关闭桌宠。

设置窗口支持 `Ctrl+A` 全选输入框、`Enter` 保存、`Esc` 取消；账号密码模式下可以临时显示密码，密码仍只保存到 Windows 凭据库，不写入 `config.json`。

默认呈现更接近 Codex pet 的 atlas 运行方式：

- 只播放每行动画里的非空格，避免空白帧闪烁。
- 默认 `window_scale` 为 `0.5`，显示约 `96x104`。
- 默认 `active_state` 为 `idle`，不会在 GPU 忙碌时一直播放 `running`。
- 会把半透明边缘转成二值透明，避免 Tk 透明窗产生紫色边框。
- GPU 空闲时不再切到趴下/等待姿态，而是在普通 idle 动画上叠加闭眼、轻微点头和浅蓝色睡眠泡泡。
- 拖动宠物时会临时切到 `running-right` / `running-left` 动画，方向随鼠标移动立刻变化；慢拖约 `150ms` 一帧，快拖会放慢到约 `200ms`，避免鼠标事件过密导致动画跑太快。

本地无服务器时可以用模拟数据预览：

```powershell
.\run.ps1 -Mock
```

右键 Rowlet 或按 `Esc` 退出；按住左键可以拖动窗口。

## 判定规则

每次轮询执行远程 `nvidia-smi`，读取 GPU index、UUID、利用率、显存占用和计算进程。单张卡满足以下条件时视为空闲：

- GPU 利用率 `<= idle_util_threshold`
- 显存占用 `<= idle_memory_threshold_mb`
- 没有映射到该 GPU 的计算进程

多卡规则是“任意一张卡空闲即提示”，气泡会列出空闲和忙碌 GPU，例如 `GPU 1、GPU 3 目前空闲；GPU 0、GPU 2 正忙。`
