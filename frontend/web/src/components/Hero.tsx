import { motion } from "motion/react";
import { ArrowRight } from "lucide-react";
import { MusicStaff } from "./MusicStaff";

const GITHUB_URL = "https://github.com/eatbas/symphony-api";

/** Floating music note with randomised position and animation. */
function FloatingNote({
  note,
  style,
  delay,
}: {
  note: string;
  style: React.CSSProperties;
  delay: number;
}) {
  return (
    <motion.span
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: [0, 0.15, 0.15, 0], y: -60 }}
      transition={{
        duration: 6,
        delay,
        repeat: Infinity,
        repeatDelay: 2,
        ease: "easeInOut",
      }}
      className="pointer-events-none absolute select-none text-4xl text-neutral-300 sm:text-5xl"
      style={style}
      aria-hidden="true"
    >
      {note}
    </motion.span>
  );
}

const NOTES: { note: string; x: string; y: string; delay: number }[] = [
  { note: "♪", x: "10%", y: "20%", delay: 0 },
  { note: "♫", x: "85%", y: "15%", delay: 1.5 },
  { note: "♩", x: "75%", y: "70%", delay: 3 },
  { note: "♬", x: "20%", y: "75%", delay: 2.2 },
  { note: "♪", x: "50%", y: "10%", delay: 4 },
  { note: "♫", x: "60%", y: "80%", delay: 1 },
  { note: "♩", x: "35%", y: "55%", delay: 3.5 },
];

export function Hero() {
  return (
    <section className="relative flex min-h-screen items-center justify-center overflow-hidden bg-white px-6 pt-24">
      {/* Floating music notes */}
      {NOTES.map((n, i) => (
        <FloatingNote
          key={i}
          note={n.note}
          style={{ left: n.x, top: n.y }}
          delay={n.delay}
        />
      ))}

      {/* Beethoven "Ode to Joy" staff background */}
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-black opacity-[0.06]">
        <MusicStaff />
      </div>

      <div className="relative z-10 mx-auto max-w-4xl text-center">
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.2 }}
        >
          <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-neutral-200 bg-neutral-50 px-4 py-1.5 text-xs font-medium tracking-wide text-neutral-600 uppercase">
            <span className="h-1.5 w-1.5 rounded-full bg-black" />
            Open-Source CLI Orchestra
          </span>
        </motion.div>

        <motion.h1
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.35 }}
          className="mt-8 text-5xl font-black leading-[1.1] tracking-tight text-black sm:text-7xl lg:text-8xl"
        >
          Orchestrate
          <br />
          <span className="text-neutral-400">Your AI Ensemble</span>
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.5 }}
          className="mx-auto mt-8 max-w-2xl text-lg leading-relaxed text-neutral-500 sm:text-xl"
        >
          One unified API that conducts{" "}
          <strong className="text-black">Antigravity</strong>,{" "}
          <strong className="text-black">Codex</strong>,{" "}
          <strong className="text-black">Claude</strong>,{" "}
          <strong className="text-black">Kimi</strong>,{" "}
          <strong className="text-black">Copilot</strong>, and{" "}
          <strong className="text-black">OpenCode</strong>{" "}
          as warm, pre-spawned CLI musicians — ready to play on demand.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.65 }}
          className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row"
        >
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="group flex items-center gap-2 rounded-2xl bg-black px-8 py-4 text-base font-semibold text-white transition-all duration-200 hover:bg-neutral-800 cursor-pointer"
          >
            Get Started
            <ArrowRight className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1" />
          </a>
          <a
            href="#features"
            className="flex items-center gap-2 rounded-2xl border border-neutral-200 bg-white px-8 py-4 text-base font-semibold text-black transition-all duration-200 hover:border-neutral-400 cursor-pointer"
          >
            Explore Features
          </a>
        </motion.div>
      </div>
    </section>
  );
}
