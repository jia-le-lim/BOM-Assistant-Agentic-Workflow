import type { NextStepPrediction } from "@/lib/types";

export function NextStepSuggestions({ prediction, disabled, onSelect }: {
  prediction: NextStepPrediction;
  disabled?: boolean;
  onSelect: (prompt: string) => void;
}) {
  if (prediction.suggestions.length === 0) return null;

  return (
    <section className="next-step-suggestions" aria-label="Predicted next steps">
      <div className="next-step-heading">
        <span className="next-step-spark" aria-hidden>✦</span>
        <div>
          <strong>What would you like to do next?</strong>
          <span>{prediction.intent || "Suggestions based on your first question"}</span>
        </div>
      </div>
      <div className="next-step-options">
        {prediction.suggestions.map((suggestion) => (
          <button key={suggestion.prompt} type="button" disabled={disabled}
                  aria-label={`Ask: ${suggestion.prompt}`}
                  onClick={() => onSelect(suggestion.prompt)}>
            <span>
              <strong>{suggestion.label}</strong>
              <small>{suggestion.prompt}</small>
            </span>
            <span aria-hidden>↗</span>
          </button>
        ))}
      </div>
      <p>Predicted by the model from this conversation—not a fixed workflow.</p>
    </section>
  );
}
