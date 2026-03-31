import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "btn-primary-gradient shadow-orange-glow text-zinc-900 rounded-full hover:shadow-2xl hover:shadow-zinc-900/20 hover:-translate-y-0.5 tracking-tight",
        destructive:
          "bg-destructive text-destructive-foreground rounded-full hover:bg-destructive/90",
        outline:
          "border border-zinc-200 bg-white text-zinc-600 rounded-full shadow-genlabs hover:bg-zinc-50 hover:-translate-y-0.5 hover:shadow-xl",
        secondary:
          "bg-gradient-to-b from-black/5 via-black/10 to-black/5 text-zinc-600 rounded-full shadow-genlabs hover:bg-zinc-50 tracking-tight",
        ghost:
          "text-zinc-500 rounded-xl hover:bg-zinc-100 hover:text-zinc-900",
        link: "text-zinc-900 underline-offset-4 hover:underline",
      },
      size: {
        default: "h-11 px-6 py-3",
        sm: "h-9 px-4 py-2 text-xs",
        lg: "h-12 px-8 py-3",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
