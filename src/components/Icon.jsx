import React from "react";
import {
  MessageSquare,   // chat
  Workflow,         // workflow
  ListChecks,      // tasks
  ShoppingBag,      // market
  Puzzle,           // skills
  Settings,         // settings
  Trash2,          // trash
  Plus,             // plus
  Search,           // search
  ChevronRight,     // chevron
  Film,             // film
  Image,            // image
  X,                // close / x
  Check,            // check
  CircleCheck,      // check-circle
  TriangleAlert,     // warning
  CircleAlert,      // alert
  Info,             // info
  FileText,         // file / doc
  Folder,           // folder
  FolderOpen,       // folder-open
  ClipboardList,    // clipboard / list
  KeyRound,         // key
  Play,             // play / run
  Hourglass,        // loader / wait
  RotateCw,         // refresh
  Volume2,         // audio / mic
  Mic,              // mic
  Palette,          // palette / design
  Wrench,           // tools
  Shield,           // shield / secure
  PenLine,          // pen / edit
  Monitor,          // desktop / monitor
  Heart,            // heart / like
  NotebookPen,      // note / memo
  PanelRight,       // panel toggle
  ArrowDown,        // arrow-down
  CircleDot,        // dot (filled)
  Circle,           // circle (outline)
  Square,           // square
  Target,           // target / goal
  List,             // list
  Send,             // send
  Pause,            // pause / stop
  Zap,              // zap / bolt
  Bot,              // robot / agent
  Brain,            // brain / thinking
  Sparkles,         // sparkles
  Package,          // package
  Cpu,              // cpu
  MoreHorizontal,    // more
  Filter,           // filter
  ExternalLink,     // external-link
  Copy,             // copy
  Lock,             // lock
  Globe,            // globe
  Terminal,         // terminal
  GitBranch,        // git-branch
  SlidersHorizontal, // sliders
  Bell,             // bell
  User,             // user
  Loader2,          // loader spinner
  RefreshCw,        // refresh-cw
  ThumbsUp,        // thumbs-up
  ThumbsDown,      // thumbs-down
  Share2,          // share
  StopCircle,      // stop-circle
  CheckCheck,      // check-check / done
  BarChart3,       // bar-chart / usage stats
  AppWindow,       // app-window / "move tab to new window"
  Maximize2,       // maximize-2 (detach)
  Home,            // home / launcher
} from "lucide-react";

// Single source of truth: name -> Lucide component.
const MAP = {
  chat: MessageSquare,
  workflow: Workflow,
  tasks: ListChecks,
  market: ShoppingBag,
  skills: Puzzle,
  settings: Settings,
  trash: Trash2,
  plus: Plus,
  search: Search,
  chevron: ChevronRight,
  film: Film,
  image: Image,

  close: X,
  x: X,
  check: Check,
  "check-circle": CircleCheck,
  warning: TriangleAlert,
  alert: CircleAlert,
  info: Info,

  file: FileText,
  doc: FileText,
  folder: Folder,
  "folder-open": FolderOpen,
  clipboard: ClipboardList,
  key: KeyRound,
  play: Play,
  run: Play,
  loader: Hourglass,
  wait: Hourglass,
  refresh: RotateCw,
  audio: Volume2,
  mic: Mic,
  palette: Palette,
  tools: Wrench,
  wrench: Wrench,
  shield: Shield,
  secure: Shield,
  pen: PenLine,
  edit: PenLine,
  monitor: Monitor,
  desktop: Monitor,
  heart: Heart,
  like: Heart,
  note: NotebookPen,
  memo: NotebookPen,
  panel: PanelRight,
  "arrow-down": ArrowDown,
  down: ArrowDown,

  dot: CircleDot,
  circle: Circle,
  square: Square,
  target: Target,
  list: List,
  send: Send,
  pause: Pause,
  stop: Pause,
  zap: Zap,
  bolt: Zap,
  bot: Bot,
  agent: Bot,
  robot: Bot,
  brain: Brain,
  thinking: Brain,
  sparkles: Sparkles,
  package: Package,
  cpu: Cpu,
  more: MoreHorizontal,
  filter: Filter,
  external: ExternalLink,
  copy: Copy,
  lock: Lock,
  globe: Globe,
  terminal: Terminal,
  "git-branch": GitBranch,
  sliders: SlidersHorizontal,
  bell: Bell,
  user: User,
  spinner: Loader2,
  "thumbs-up": ThumbsUp,
  "thumbs-down": ThumbsDown,
  share: Share2,
  "stop-circle": StopCircle,
  "check-check": CheckCheck,
  activity: BarChart3,
  window: AppWindow,
  "app-window": AppWindow,
  detach: AppWindow,
  "maximize-2": Maximize2,
  home: Home,
  launcher: Home,
  // neutral fallback for unknown names
  default: CircleDot,
};

export default function Icon({ name = "default", size = 16, className, strokeWidth = 2, style }) {
  const Comp = MAP[name] || MAP.default;
  return (
    <Comp
      width={size}
      height={size}
      size={size}
      className={className}
      strokeWidth={strokeWidth}
      style={style}
      aria-hidden="true"
    />
  );
}
