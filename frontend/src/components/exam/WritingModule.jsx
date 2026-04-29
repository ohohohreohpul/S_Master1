import { useState } from "react";
import { Pen, AlertCircle } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function WritingModule({ exam, answers, updateAnswer }) {
  const [activeTask, setActiveTask] = useState("1");
  const tasks = exam?.writing?.tasks || [];

  const wordCount = (text) => {
    if (!text || !text.trim()) return 0;
    return text.trim().split(/\s+/).length;
  };

  return (
    <div className="max-w-5xl mx-auto p-8" data-testid="writing-module">
      <Tabs value={activeTask} onValueChange={setActiveTask}>
        <TabsList className="mb-6 bg-[var(--ps-ice)]">
          {tasks.map(t => (
            <TabsTrigger key={t.task_num} value={String(t.task_num)} className="data-[state=active]:bg-[var(--ps-blue)] data-[state=active]:text-white">
              Task {t.task_num} ({t.time_minutes} min)
            </TabsTrigger>
          ))}
        </TabsList>

        {tasks.map(task => {
          const key = `task_${task.task_num}`;
          const text = answers[key] || "";
          const wc = wordCount(text);
          const belowMin = wc < task.min_words;

          return (
            <TabsContent key={task.task_num} value={String(task.task_num)}>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                {/* Prompt */}
                <div className="card-ps p-6" data-testid={`writing-prompt-${task.task_num}`}>
                  <div className="flex items-center gap-2 mb-4">
                    <Pen size={18} className="text-[var(--ps-blue)]" />
                    <h3 className="font-medium">Task {task.task_num}</h3>
                    <span className="text-xs text-[var(--ps-body-gray)]">({task.time_minutes} minutes)</span>
                  </div>
                  <div className="text-sm leading-relaxed" style={{ color: "var(--ps-charcoal)" }}>
                    {task.prompt.split("\n").map((line, i) => (
                      <p key={i} className="mb-2">{line}</p>
                    ))}
                  </div>
                  <div className="mt-4 p-3 bg-[var(--ps-ice)] rounded-lg">
                    <p className="text-xs text-[var(--ps-body-gray)]">Minimum words: {task.min_words}</p>
                  </div>
                </div>

                {/* Editor */}
                <div data-testid={`writing-editor-${task.task_num}`}>
                  <textarea
                    className="w-full h-96 p-4 border border-[var(--ps-divider)] rounded-xl text-sm leading-relaxed resize-none focus:outline-none focus:border-[var(--ps-blue)] focus:ring-2 focus:ring-[var(--ps-blue)]/20 font-inherit"
                    placeholder="Start writing your response here..."
                    value={text}
                    onChange={(e) => updateAnswer(key, e.target.value)}
                    data-testid={`writing-textarea-${task.task_num}`}
                  />
                  <div className="flex items-center justify-between mt-3">
                    <div className="flex items-center gap-2">
                      <span className={`text-sm font-medium ${belowMin ? "text-[var(--ps-error)]" : "text-[var(--ps-blue)]"}`}>
                        {wc} words
                      </span>
                      {belowMin && (
                        <span className="flex items-center gap-1 text-xs text-[var(--ps-error)]">
                          <AlertCircle size={12} /> Minimum {task.min_words} words required
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-[var(--ps-body-gray)]">
                      {wc >= task.min_words ? "Minimum word count reached" : `${task.min_words - wc} more words needed`}
                    </span>
                  </div>
                </div>
              </div>
            </TabsContent>
          );
        })}
      </Tabs>
    </div>
  );
}
