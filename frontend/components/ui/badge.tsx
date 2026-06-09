import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-neutral-950",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-blue-400/15 text-blue-200",
        secondary:
          "border-white/10 bg-white/5 text-neutral-300 backdrop-blur",
        destructive:
          "border-red-400/30 bg-red-500/15 text-red-200",
        outline:
          "border-white/15 text-neutral-300",
        success:
          "border-emerald-400/30 bg-emerald-500/15 text-emerald-200",
        warning:
          "border-amber-400/30 bg-amber-500/15 text-amber-200",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
