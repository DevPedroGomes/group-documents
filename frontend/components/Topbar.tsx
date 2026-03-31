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
      <header className="sticky top-0 z-40 w-full border-b border-white/20">
        <div className="glass-panel">
          <div className="flex h-14 items-center justify-between px-4 lg:px-6">
            {/* Logo */}
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center h-8 w-8 rounded-full btn-primary-gradient shadow-orange-glow/50">
                <FileStack className="h-4 w-4 text-zinc-900" />
              </div>
              <span className="font-semibold text-zinc-900 tracking-tight">Document Hub</span>
            </div>

            {/* User section */}
            <div className="flex items-center gap-3">
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex items-center gap-2 bg-white/60 rounded-full px-3 py-1.5 border border-white/60 backdrop-blur-sm shadow-sm">
                    <Avatar className="h-6 w-6">
                      <AvatarFallback className="text-[10px] font-bold bg-zinc-900 text-white">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                    <span className="text-xs font-medium text-zinc-600 hidden sm:inline-block max-w-[150px] truncate tracking-tight">
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
                className="h-8 w-8 rounded-full text-zinc-400 hover:text-zinc-900 hover:bg-zinc-100"
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
