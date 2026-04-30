# 完整流程示例

## 示例一：多人对话播客

```
用户：生成一个5分钟的AI主题播客，主持人和嘉宾对话

Agent 第1步 - 脚本创作：
生成完整对话脚本（主持人 + 嘉宾，约1200字）
→ 输出脚本，询问："脚本是否满意？"
→ 等待用户确认

用户：可以，就这个

Agent 第2步 - 音色设计：
调用 qwen_voice_design(voice_description="温柔知性的女主持人", text="大家好，欢迎来到今天的节目")
→ 获得 audio_url_host（主持人音色样本）
调用 qwen_voice_design(voice_description="沉稳睿智的男嘉宾", text="很高兴能来到这里")
→ 获得 audio_url_guest（嘉宾音色样本）
→ 展示两个音色样本，询问："请试听音色样本，是否满意？"

用户：主持人音色可以，嘉宾声音再沉稳一点

Agent：重新调用 qwen_voice_design 调整嘉宾音色描述，生成新样本
→ 再次询问确认

用户：好的，可以了

Agent 第3步 - 批量合成：
主持人所有对话 → 逐句调用 qwen_voice_cloning(reference_audio=audio_url_host, text=每句话)
嘉宾所有对话 → 逐句调用 qwen_voice_cloning(reference_audio=audio_url_guest, text=每句话)

Agent 第4步 - BGM选择：
调用 select_background_music(scene_description="科技感、轻松的播客背景音乐")

Agent 第5步 - 音频拼接：
调用 concatenate_audio(audio_paths=[...所有片段路径...], crossfade_duration=200, silence_duration=1200)

Agent 第6步 - 混音输出：
调用 mix_audio_with_bgm(voice_audio=拼接后路径, bgm_audio=BGM路径, bgm_volume=-26, intro_duration=4, normalize=True)
→ 完成！输出最终音频文件
```

---

## 示例二：独白播客

```
用户：帮我做一个3分钟的产品介绍独白音频，普通话女声，偏商务风

Agent 第1步 - 脚本创作：
生成独白脚本（约700字，商务介绍风格）
→ 输出脚本，等待确认

用户：脚本OK，稍微加一点亲切感

Agent：调整脚本语气
→ 再次输出修改版脚本

用户：可以了

Agent 第2步 - 音色设计：
调用 qwen_voice_design(voice_description="标准普通话，商务亲切的女声", text="大家好，今天很高兴和大家介绍...")
→ 生成音色样本，询问确认

用户：好的，就这个

Agent 第3步 - 批量合成：
按段落逐段调用 qwen_voice_cloning(reference_audio=audio_url, text=每段文本)

Agent 第4步 - BGM（可询问用户）：
"是否需要添加背景音乐？"

用户：不需要了

Agent 第5步 - 音频拼接：
调用 concatenate_audio（仅人声片段，silence_duration=800ms）
→ 完成！输出最终音频
```

---

## 示例三：用户提供参考音频（声音克隆）

```
用户：我上传了一段我自己的声音，帮我用这个声音生成一段语音教程

Agent 第2步 - 音色来源：
检测到用户上传了参考音频 → 使用 qwen_voice_cloning 克隆
调用 qwen_voice_cloning(reference_audio=用户上传路径, text="测试一下，这是克隆的声音效果")
→ 生成克隆样本，询问："克隆效果是否符合您的预期？"

用户：很接近，继续

（后续步骤同独白播客流程）
```
