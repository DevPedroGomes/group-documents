"use client";
export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  return (
    <div className="p-8 text-center">
      <div className="font-semibold mb-2">Something went wrong.</div>
      <div className="text-sm opacity-70 mb-4">{error.message}</div>
      <button
        onClick={reset}
        className="px-4 py-2 rounded-xl bg-black text-white"
      >
        Try again
      </button>
    </div>
  );
}
