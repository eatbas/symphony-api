import { AnimatedSection } from "./AnimatedSection";

interface ModelChip {
  label: string;
  isFree?: boolean;
}

interface Provider {
  name: string;
  cli: string;
  models: ModelChip[];
  resume: boolean;
}

function chip(label: string, isFree = false): ModelChip {
  return { label, isFree };
}

const PROVIDERS: Provider[] = [
  {
    name: "Antigravity",
    cli: "agy",
    models: [chip("gemini-3.5-flash"), chip("gemini-3.1-pro")],
    resume: false,
  },
  {
    name: "Codex",
    cli: "codex",
    models: [chip("gpt-5.4"), chip("gpt-5.4-mini")],
    resume: true,
  },
  {
    name: "Claude",
    cli: "claude",
    models: [chip("opus"), chip("sonnet"), chip("haiku")],
    resume: true,
  },
  {
    name: "Kimi",
    cli: "kimi",
    models: [chip("kimi-code"), chip("kimi-for-coding")],
    resume: true,
  },
  {
    name: "OpenCode · OpenRouter",
    cli: "opencode",
    models: [
      chip("qwen3-coder", true),
      chip("deepseek-v4-flash", true),
      chip("nemotron-3-super-120b", true),
      chip("+ live top-10"),
    ],
    resume: true,
  },
];

export function Providers() {
  return (
    <section id="providers" className="bg-white px-6 py-32">
      <div className="mx-auto max-w-6xl">
        <AnimatedSection className="text-center">
          <span className="text-sm font-medium tracking-wide text-neutral-500 uppercase">
            Instruments
          </span>
          <h2 className="mt-4 text-4xl font-bold tracking-tight text-black sm:text-5xl">
            Five providers, one podium
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-neutral-500">
            Each provider is a first-class musician with its own CLI adapter,
            model roster, and session resume capability.
          </p>
        </AnimatedSection>

        <div className="mt-20 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {PROVIDERS.map((provider, i) => (
            <AnimatedSection key={provider.name} delay={i * 0.08}>
              <div className="group h-full rounded-2xl border border-neutral-200 bg-white p-6 transition-all duration-300 hover:border-black hover:shadow-lg cursor-pointer">
                <div className="flex items-center justify-between">
                  <h3 className="text-xl font-bold text-black">
                    {provider.name}
                  </h3>
                  <code className="rounded-lg bg-neutral-100 px-3 py-1 text-xs font-mono text-neutral-600">
                    {provider.cli}
                  </code>
                </div>

                <div className="mt-4 flex flex-wrap gap-1.5">
                  {provider.models.map((model) => (
                    <span
                      key={model.label}
                      className={
                        "rounded-md px-2.5 py-1 text-xs font-medium " +
                        (model.isFree
                          ? "bg-emerald-600 text-white"
                          : "bg-neutral-950 text-white")
                      }
                    >
                      {model.label}
                      {model.isFree ? " · FREE" : ""}
                    </span>
                  ))}
                </div>

                <div className="mt-4 flex items-center gap-1.5 text-xs text-neutral-500">
                  <span
                    className={
                      "h-2 w-2 rounded-full " +
                      (provider.resume ? "bg-green-500" : "bg-neutral-300")
                    }
                  />
                  {provider.resume ? "Resume supported" : "New sessions only"}
                </div>
              </div>
            </AnimatedSection>
          ))}
        </div>
      </div>
    </section>
  );
}
