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
      <header className="sticky top-0 z-40 w-full border-b border-white/10">
        <div className="glass-panel">
          <div className="flex h-14 items-center justify-between px-4 lg:px-6">
            {/* Logo */}
            <div className="flex items-center gap-3">
              <div
                className="flex items-center justify-center h-8 w-8 rounded-full bg-white/10 backdrop-blur border-gradient"
                style={{ borderRadius: 9999 }}
              >
                <FileStack className="h-4 w-4 text-white" />
              </div>
              <span className="font-semibold text-white tracking-tight">BrainHub</span>
            </div>

            {/* User section */}
            <div className="flex items-center gap-3">
              <Tooltip>
                <TooltipTrigger asChild>
                  <div
                    className="flex items-center gap-2 bg-white/5 rounded-full px-3 py-1.5 border-gradient backdrop-blur"
                    style={{ borderRadius: 9999 }}
                  >
                    <Avatar className="h-6 w-6">
                      <AvatarFallback className="text-[10px] font-bold bg-blue-400/20 text-blue-300">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                    <span className="text-xs font-medium text-neutral-300 hidden sm:inline-block max-w-[150px] truncate tracking-tight">
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
                className="h-8 w-8 rounded-full text-neutral-400 hover:text-white hover:bg-white/5"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
      </header>
    </TooltipProvider>
  )
}
