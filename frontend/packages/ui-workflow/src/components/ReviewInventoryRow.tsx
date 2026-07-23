/**
 * Shared inventory summary boxes for review panels (findings + proposals).
 */

export type ReviewInventoryTone =
  | 'total'
  | 'auto'
  | 'ai'
  | 'manual'
  | 'pending'
  | 'accepted'
  | 'declined';

export interface ReviewInventoryBox {
  key: string;
  label: string;
  primary: number;
  secondary: number;
  primaryCaption?: string;
  secondaryCaption?: string;
  tone: ReviewInventoryTone;
}

export interface ReviewInventoryRowProps {
  boxes: ReviewInventoryBox[];
  /** Accessible name for the inventory group. */
  ariaLabel?: string;
}

/** Compact equal-sized inventory boxes (findings / locations per category). */
export function ReviewInventoryRow({
  boxes,
  ariaLabel = 'Review inventory',
}: ReviewInventoryRowProps) {
  return (
    <div
      className="apme-review-inventory"
      role="group"
      aria-label={ariaLabel}
    >
      {boxes.map((box) => (
        <div
          key={box.key}
          className={`apme-review-inventory-box apme-review-inventory-box--${box.tone}`}
        >
          <div className="apme-review-inventory-box__label">{box.label}</div>
          <div className="apme-review-inventory-box__nums">
            <span className="apme-review-inventory-box__primary">
              {box.primary}
            </span>
            <span className="apme-review-inventory-box__secondary">
              {box.secondary}
            </span>
          </div>
          <div className="apme-review-inventory-box__captions">
            <span>{box.primaryCaption ?? 'findings'}</span>
            <span>{box.secondaryCaption ?? 'locations'}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
