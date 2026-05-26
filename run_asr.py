import os
import sys

# ==========================================
# 核心设定 1：在代码最开始，强制把模型下载路径指向 D:\models
# ==========================================
os.environ["MODELSCOPE_CACHE"] = r"D:\models"
os.environ["HF_HOME"] = r"D:\models"

# 引入 FunASR
from funasr import AutoModel

def main():
    # 你的音频文件放在 D:\test 目录下
    audio_filename = "05月20日.mp4"  # 👈 记得改成你实际的音频文件名和格式
    base_folder=r"D:\DanmakuRender\Tasks文件\不可一世杀手（弹幕版）"
    input_audio_path = os.path.join(base_folder, audio_filename)

    if not os.path.exists(input_audio_path):
        print(f"❌ 错误：没有找到文件 {input_audio_path}，请先放进去！")
        sys.exit(1)

    print("====================================")
    print("🚀 正在初始化 FunASR 黄金组合 Pipeline...")
    print("💡 提示：首次运行会下载 4 个模型到 D:\\models，耗时较长，请耐心等待进度条...")
    print("====================================")

    # ==========================================
    # 核心设定 2：组装最适合王者荣耀分析的 4 合 1 黄金组合
    # ==========================================
    model = AutoModel(
        model="paraformer-zh",             # ASR 核心大脑：负责转文字和拿时间戳 (Timestamp)
        model_revision="v2.0.4",
        vad_model="fsmn-vad",              # VAD 语音切片：负责过滤游戏背景音和 BGM
        vad_model_revision="v2.0.4",
        punc_model="ct-punc-c",            # Punc 自动标点：给嘴臭和阴阳怪气的内容加标点
        punc_model_revision="v2.0.4",
        spk_model="cam++",                 # SPK 声纹识别：核心！负责把主播和队友的声音“分家” (Speaker Diarization)
        spk_model_revision="v2.0.2"
    )

    print("\n🎉 模型加载成功！开始分析音频内容...")

    # ==========================================
    # 核心设定 3：运行推理 (Inference)
    # ==========================================
    # batch_size_s=300 表示每 300 秒（5分钟）动态切片处理一次，极大防止 4 小时大视频导致显存/内存爆掉 (OOM)
    results = model.generate(
        input=input_audio_path,
        batch_size_s=300,
        vad_kwargs={"max_single_segment_time": 30000}
        # hotword="吃线" # 热词激励 (Hotwords)，提高王者游戏高频词准确率
    )

    # ==========================================
    # 核心设定 4：将提取出的数据结构化保存，方便后面喂给大模型
    # ==========================================
    output_text_path = os.path.join(base_folder, "step1_asr_result.txt")

    print(f"\n💾 正在将结构化文本写入：{output_text_path}")

    with open(output_text_path, "w", encoding="utf-8") as f:
        for item in results:
            # FunASR + spk_model 的句子列表在 sentence_info 里
            sentences = item.get("sentence_info", [])

            if not sentences:
                # 兜底：如果没有 sentence_info，直接写原始文本
                f.write(item.get("text", str(item)) + "\n")
                continue

            for sentence in sentences:
                spk   = sentence.get("spk", "?")
                start = sentence.get("start", 0)
                end   = sentence.get("end", 0)
                text  = sentence.get("text", "")

                start_time = f"{int(start//3600000):02d}:{int((start%3600000)//60000):02d}:{int((start%60000)//1000):02d}"
                end_time   = f"{int(end//3600000):02d}:{int((end%3600000)//60000):02d}:{int((end%60000)//1000):02d}"

                f.write(f"[{start_time} --> {end_time}] spk_{spk}: {text}\n")

    print("====================================")
    print("✨ Step 1 提取完成！")
    print(f"请打开 {output_text_path} 查看带有角色标签和精准时间戳的《对线剧本》。")
    print("接下来你就可以把这个文本中的高能段落复制进任意 LLM（如 DeepSeek/Qwen）进行情感和阴阳怪气的深度分析了！")
    print("====================================")

if __name__ == "__main__":
    main()