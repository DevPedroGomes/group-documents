'use client'

import { Button } from '@/components/ui/button'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { LogOut, FileStack } from 'lucide-react'

interface TopbarProps {
  email?: string
  onSignOut: () => void
}

export default function Topbar({ email, onSignOut }: TopbarProps) {
  const initials = email
    ? email.split('@')[0].slice(0, 2).toUpperCase()
    : '?'

  return (
    <TooltipProvider>
      <header className="sticky top-0 z-40 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex h-14 items-center justify-between px-4 lg:px-6">
          {/* Logo / Brand */}
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center h-8 w-8 rounded-lg bg-primary text-primary-foreground">
              <FileStack className="h-4 w-4" />
            </div>
            <span className="font-semibold tracking-tight">Document Hub</span>
          </div>

          {/* User section */}
          <div className="flex items-center gap-3">
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex items-center gap-2">
                  <Avatar className="h-8 w-8">
                    <AvatarFallback className="text-xs bg-muted">
                      {initials}
                    </AvatarFallback>
                  </Avatar>
                  <span className="text-sm text-muted-foreground hidden sm:inline-block max-w-[150px] truncate">
                    {email}
                  </span>
                </div>
              </TooltipTrigger>
              <TooltipContent>
                <p>{email}</p>
              </TooltipContent>
            </Tooltip>

            <Button
              variant="ghost"
              size="icon"
              onClick={onSignOut}
              className="h-8 w-8"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </header>
    </TooltipProvider>
  )
}
