import re

html_content = r"""<!DOCTYPE html>
<html class="dark" lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Sinyal | Intelligence Terminal</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<script id="tailwind-config">
        tailwind.config = {
            darkMode: "class",
            theme: {
                extend: {
                    colors: {
                        "surface-bright": "#37393e",
                        "outline-variant": "#564337",
                        "surface-container": "#1e2024",
                        "tertiary-container": "#9a9996",
                        "secondary-fixed-dim": "#c6c6cc",
                        "on-primary-fixed-variant": "#713700",
                        "on-error": "#690005",
                        "background": "#111318",
                        "tertiary-fixed-dim": "#c8c6c2",
                        "on-tertiary-fixed-variant": "#474744",
                        "surface-tint": "#ffb783",
                        "on-tertiary-container": "#31312f",
                        "on-surface": "#e2e2e8",
                        "error-container": "#93000a",
                        "inverse-surface": "#e2e2e8",
                        "on-tertiary-fixed": "#1c1c1a",
                        "surface-dim": "#111318",
                        "on-secondary-fixed-variant": "#45474b",
                        "primary-container": "#e67e22",
                        "secondary-fixed": "#e2e2e8",
                        "secondary": "#c6c6cc",
                        "tertiary-fixed": "#e5e2de",
                        "surface-container-low": "#1a1c20",
                        "surface": "#111318",
                        "on-primary": "#4f2500",
                        "outline": "#a48c7d",
                        "on-secondary-fixed": "#1a1c20",
                        "secondary-container": "#47494e",
                        "inverse-on-surface": "#2f3035",
                        "primary": "#ffb783",
                        "on-secondary": "#2f3035",
                        "on-surface-variant": "#dcc1b1",
                        "error": "#ffb4ab",
                        "surface-container-highest": "#333539",
                        "primary-fixed": "#ffdcc5",
                        "on-background": "#e2e2e8",
                        "surface-container-lowest": "#0c0e12",
                        "surface-container-high": "#282a2e",
                        "primary-fixed-dim": "#ffb783",
                        "tertiary": "#c8c6c2",
                        "on-tertiary": "#31302e",
                        "on-secondary-container": "#b7b8be",
                        "on-primary-fixed": "#301400",
                        "on-error-container": "#ffdad6",
                        "surface-variant": "#333539",
                        "on-primary-container": "#502600",
                        "inverse-primary": "#944a00"
                    },
                    fontFamily: {
                        "headline": ["Manrope"],
                        "body": ["Inter"],
                        "label": ["Inter"]
                    },
                    borderRadius: {"DEFAULT": "0.25rem", "lg": "0.5rem", "xl": "0.75rem", "full": "9999px"},
                },
            },
        }
    </script>
<style>
        .material-symbols-outlined {
            font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
            vertical-align: middle;
        }
        body {
            background-color: #111318;
            color: #e2e2e8;
            font-family: 'Inter', sans-serif;
        }
        .custom-scrollbar::-webkit-scrollbar {
            width: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
            background: #0c0e12;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
            background: #333539;
            border-radius: 10px;
        }
        
        .tab-btn.active {
            background-color: rgba(230,126,34,0.1);
            color: #ffb783;
            border-right-width: 4px;
            border-color: #e67e22;
        }
        
        /* Specific resets for select and input */
        .ds-input {
            background-color: #0c0e12;
            border: 1px solid rgba(86,67,55,0.2);
            color: #e2e2e8;
            border-radius: 0.75rem;
            font-size: 0.875rem;
        }
        .ds-input:focus {
            outline: none;
            border-color: rgba(255, 183, 131, 0.5);
            box-shadow: 0 0 0 1px rgba(255, 183, 131, 0.5);
        }
    </style>
</head>
<body class="bg-background text-on-surface custom-scrollbar">

<div class="flex h-screen overflow-hidden">
    <!-- SideNavBar Component -->
    <aside class="fixed left-0 top-0 h-full flex flex-col p-4 z-40 bg-[#0c0e12] w-64 shadow-2xl transition-all border-r border-outline-variant/10">
        <div class="mb-8 px-4 flex items-center gap-3">
            <div class="w-10 h-10 bg-primary-container rounded-lg flex items-center justify-center">
                <span class="material-symbols-outlined text-on-primary-fixed" data-icon="insights">insights</span>
            </div>
            <div>
                <h1 class="text-xl font-extrabold text-[#ffb783] font-headline tracking-tight">Sinyal</h1>
                <p class="text-[10px] uppercase tracking-widest text-on-surface-variant font-bold">Editorial Intel</p>
            </div>
        </div>
        
        <nav class="flex-1 space-y-1">
            <button class="tab-btn active w-full flex items-center gap-3 text-[#949494] rounded-xl px-4 py-3 font-manrope font-semibold text-sm transition-all hover:bg-[#1a1c20] hover:text-[#ffb783]" onclick="switchTab('dashboard', this)">
                <span class="material-symbols-outlined" data-icon="insights">insights</span>
                <span>Intelligence</span>
            </button>
            <button class="tab-btn w-full flex items-center gap-3 text-[#949494] px-4 py-3 rounded-xl border-r-4 border-transparent font-manrope font-semibold text-sm hover:bg-[#1a1c20] hover:text-[#ffb783] transition-all" onclick="switchTab('search', this)">
                <span class="material-symbols-outlined" data-icon="search">search</span>
                <span>Riset</span>
            </button>
            <button class="tab-btn w-full flex items-center gap-3 text-[#949494] px-4 py-3 rounded-xl border-r-4 border-transparent font-manrope font-semibold text-sm hover:bg-[#1a1c20] hover:text-[#ffb783] transition-all" onclick="switchTab('profile', this)">
                <span class="material-symbols-outlined" data-icon="movie_filter">movie_filter</span>
                <span>Profil</span>
            </button>
            <button class="tab-btn w-full flex items-center gap-3 text-[#949494] px-4 py-3 rounded-xl border-r-4 border-transparent font-manrope font-semibold text-sm hover:bg-[#1a1c20] hover:text-[#ffb783] transition-all" onclick="switchTab('comments', this)">
                <span class="material-symbols-outlined" data-icon="forum">forum</span>
                <span>Komentar</span>
            </button>
            
            <button class="w-full flex items-center gap-3 text-[#949494] px-4 py-3 font-manrope font-semibold text-sm hover:bg-[#1a1c20] hover:text-[#ffb783] transition-all mt-auto" onclick="window.location.href='/payment'">
                <span class="material-symbols-outlined" data-icon="settings">settings</span>
                <span>Billing</span>
            </button>
        </nav>
        
        <div class="mt-8 p-4 bg-surface-container-high rounded-xl border border-outline-variant/20">
            <p class="text-xs text-on-surface-variant mb-3">Professional analytics for the top 1% of creators.</p>
            <button class="w-full py-2.5 bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed font-bold text-sm rounded-lg hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">
                Upgrade to Pro
            </button>
        </div>
    </aside>

    <!-- Main Terminal Canvas -->
    <main class="ml-64 flex-1 flex flex-col min-w-0 bg-surface-dim">
        <header class="flex justify-between items-center w-full px-8 h-20 sticky top-0 z-50 bg-[#111318] border-b border-outline-variant/10">
            <div class="flex items-center gap-6 flex-1 max-w-2xl">
                <div class="relative w-full">
                    <span class="material-symbols-outlined absolute left-4 top-1/2 -translate-y-1/2 text-on-surface-variant">search</span>
                    <input id="globalSearch" class="ds-input w-full py-3 pl-12 pr-4" placeholder="Quick find creators or hooks..." type="text"/>
                </div>
            </div>
            <div class="flex items-center gap-4">
                <div class="flex items-center gap-1 bg-surface-container-low p-1 rounded-full">
                    <button class="p-2 text-on-surface-variant hover:text-primary transition-colors"><span class="material-symbols-outlined">notifications</span></button>
                    <button class="p-2 text-on-surface-variant hover:text-primary transition-colors"><span class="material-symbols-outlined">help_outline</span></button>
                </div>
                <div class="flex h-9 w-9 items-center justify-center rounded-full bg-primary/15 font-bold text-primary border border-primary/30">A</div>
            </div>
        </header>

        <div class="flex-1 overflow-y-auto p-8 custom-scrollbar">
            <!-- DASHBOARD TAB -->
            <section id="dashboardTab" class="tab-section">
                <!-- Terminal Hero Section -->
                <section class="mb-10">
                    <div class="flex justify-between items-end mb-6">
                        <div>
                            <span class="text-primary font-bold text-xs tracking-widest uppercase mb-2 block">Global Signal Map</span>
                            <h2 class="text-4xl font-black font-headline tracking-tighter text-on-surface">Intelligence Dashboard</h2>
                        </div>
                        <div class="flex items-center gap-3">
                            <div class="text-right">
                                <p class="text-[10px] font-bold text-on-surface-variant uppercase">Market Sentiment</p>
                                <p class="text-sm font-headline font-bold text-primary">Bullish +14.2%</p>
                            </div>
                            <div class="h-10 w-[2px] bg-outline-variant/30"></div>
                            <div class="text-right">
                                <p class="text-[10px] font-bold text-on-surface-variant uppercase">Active Signals</p>
                                <p class="text-sm font-headline font-bold text-on-surface">1,402</p>
                            </div>
                        </div>
                    </div>

                    <!-- Bento Terminal Grid -->
                    <div class="grid grid-cols-12 gap-4">
                        <div class="col-span-8 bg-surface-container-low rounded-xl p-6 border border-outline-variant/10 relative overflow-hidden h-[400px]">
                            <div class="flex justify-between items-start mb-4 relative z-10">
                                <div>
                                    <h3 class="text-lg font-bold font-headline">Viral Velocity Tracking</h3>
                                    <p class="text-xs text-on-surface-variant">Real-time content performance across platforms</p>
                                </div>
                                <div class="flex gap-2">
                                    <span class="px-2 py-1 bg-primary/10 text-primary text-[10px] font-bold rounded">LIVE</span>
                                </div>
                            </div>
                            <!-- Background Visualization -->
                            <div class="absolute inset-0 top-20 flex items-end opacity-40">
                                <div class="w-full h-full bg-gradient-to-t from-primary/20 to-transparent"></div>
                            </div>
                            <div class="absolute inset-0 top-24 flex items-center justify-center">
                                <img class="w-full h-full object-cover mix-blend-overlay opacity-30" src="https://lh3.googleusercontent.com/aida-public/AB6AXuACchwIkLsq0cX0oYFtexG3ezyeZbFOOfI548UVNEYVTvQsayUaU9LyeFC-ja8SCXA2itl0pF_xUV2lKoZIyVLGkZWZGmUz3J7jTj_-HLaBF4MQGUFO20Tr37RNd2s2X-ovs_KH1EAOY7ivsEEpleObCOthVgyQu3vvSzX3sUxxl735WgdBjrXRvz7mF-7L2UXdVGwbewz4m81k1Uyqn8JGMzjf_XboDuaw7tPaKmB5FmFY7Y3wffA2x9zIqw8dDe9sdL0jO0IVHg"/>
                                <div class="absolute bottom-10 left-10 right-10 flex justify-between">
                                    <div class="space-y-1">
                                        <p class="text-[10px] text-on-surface-variant font-bold uppercase">Peak Saturation</p>
                                        <p class="text-2xl font-bold font-headline text-primary">89.4%</p>
                                    </div>
                                    <div class="text-right space-y-1">
                                        <p class="text-[10px] text-on-surface-variant font-bold uppercase">Decay Start</p>
                                        <p class="text-2xl font-bold font-headline text-on-surface">Oct 24, 2026</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Sidebar Secondary Data -->
                        <div class="col-span-4 space-y-4">
                            <!-- Hot Trends -->
                            <div class="bg-surface-container-high rounded-xl p-5 border border-outline-variant/10">
                                <div class="flex items-center gap-2 mb-4">
                                    <span class="material-symbols-outlined text-primary" style="font-variation-settings: 'FILL' 1;">local_fire_department</span>
                                    <h4 class="text-sm font-bold uppercase tracking-tight">Emerging Hooks</h4>
                                </div>
                                <ul class="space-y-3">
                                    <li class="flex justify-between items-center group cursor-pointer">
                                        <span class="text-xs text-on-surface group-hover:text-primary transition-colors">"I didn't think I'd..."</span>
                                        <span class="text-[10px] bg-surface-container-highest px-2 py-0.5 rounded text-on-surface-variant">+240%</span>
                                    </li>
                                    <li class="flex justify-between items-center group cursor-pointer">
                                        <span class="text-xs text-on-surface group-hover:text-primary transition-colors">Lofi Productivity Hacks</span>
                                        <span class="text-[10px] bg-surface-container-highest px-2 py-0.5 rounded text-on-surface-variant">+118%</span>
                                    </li>
                                    <li class="flex justify-between items-center group cursor-pointer">
                                        <span class="text-xs text-on-surface group-hover:text-primary transition-colors">Extreme Minimalist Vlogs</span>
                                        <span class="text-[10px] bg-surface-container-highest px-2 py-0.5 rounded text-on-surface-variant">+94%</span>
                                    </li>
                                </ul>
                            </div>
                            
                            <!-- Distribution Map -->
                            <div class="bg-surface-container-lowest rounded-xl p-5 border border-outline-variant/10 h-[212px] relative overflow-hidden group">
                                <img class="absolute inset-0 w-full h-full object-cover opacity-20 grayscale group-hover:grayscale-0 transition-all duration-700" src="https://lh3.googleusercontent.com/aida-public/AB6AXuDSGXq8T1indWOeXynsfBmXbe4xILe04-bTFTPeQ3HNaIQV0T4dc--aOR9pVySZ3Mhw93aX2kW9jLhKJ3y2B_EqNCLO_5mJtUMtgKys73piNC4ylyEGavx-fyIh1u7DFtLyCSiHTFBjKSA--Id7MjQUcD_QxJJzY2Z1ngr4gH0UlO1sHMnk1lE7VqDENGhgs3Xmay5b_4Ptd7nwGvKLBZ0ZiwGpX5D6vk5sObWJaZXcRES-lJI44j1oroMmKfE8-D0GztZqqVl5lA"/>
                                <div class="relative z-10">
                                    <h4 class="text-sm font-bold uppercase tracking-tight mb-1">Global Reach</h4>
                                    <p class="text-[10px] text-on-surface-variant">Top region: SEA (Indonesia)</p>
                                </div>
                                <div class="absolute bottom-4 right-4 bg-primary/20 backdrop-blur-md px-3 py-1 rounded-full border border-primary/30">
                                    <span class="text-[10px] font-bold text-primary">MAP VIEW</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>
                
                <section>
                    <div class="flex items-center justify-between mb-4 border-b border-outline-variant/10 pb-4">
                        <div class="flex gap-6">
                            <button class="text-sm font-bold text-primary border-b-2 border-primary pb-4 -mb-[17px]">Feed Signals</button>
                            <button class="text-sm font-bold text-on-surface-variant hover:text-on-surface transition-colors pb-4">Watchlist</button>
                            <button class="text-sm font-bold text-on-surface-variant hover:text-on-surface transition-colors pb-4">Anomalies</button>
                        </div>
                    </div>
                    
                    <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10">
                        <p class="text-sm text-on-surface-variant">Gunakan navigasi "Riset" atau "Profil" di panel kiri untuk mulai memancing intelijen real-time dan menarik data konten viral.</p>
                    </div>
                </section>
            </section>

            <!-- SEARCH TAB -->
            <section id="searchTab" class="tab-section hidden">
                <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10 shadow-2xl">
                    <h3 class="font-headline text-xl font-bold">Search Workspace (Intelligence Riset)</h3>
                    <p class="mt-2 text-sm text-on-surface-variant">Cari keyword lalu analisis sinyal konten langsung di terminal.</p>
                    <div class="mt-6 grid gap-4 lg:grid-cols-4">
                        <textarea id="keywordInput" class="ds-input lg:col-span-2 p-3 min-h-[120px]" placeholder="Masukkan keyword...">openai</textarea>
                        <select id="platformSelect" class="ds-input p-3">
                            <option value="tiktok">TikTok</option>
                            <option value="youtube">YouTube</option>
                            <option value="instagram">Instagram</option>
                            <option value="twitter">X</option>
                            <option value="facebook">Facebook</option>
                        </select>
                        <select id="sortBy" class="ds-input p-3">
                            <option value="relevance">Paling relevan</option>
                            <option value="popular">Views tertinggi</option>
                            <option value="most_liked">Likes tertinggi</option>
                            <option value="latest">Terbaru</option>
                        </select>
                        <select id="dateRange" class="ds-input p-3">
                            <option value="all">Sepanjang waktu</option>
                            <option value="7d">7 hari terakhir</option>
                            <option value="30d">30 hari terakhir</option>
                        </select>
                        <input id="minViews" type="number" class="ds-input p-3" placeholder="Min views" />
                        <input id="maxViews" type="number" class="ds-input p-3" placeholder="Max views" />
                        <input id="minLikes" type="number" class="ds-input p-3" placeholder="Min likes" />
                        <input id="maxLikes" type="number" class="ds-input p-3" placeholder="Max likes" />
                    </div>
                    <div class="mt-6 flex flex-wrap gap-3">
                        <button id="searchBtn" class="bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed rounded-xl px-5 py-3 text-sm font-bold hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">Scan Sinyal</button>
                        <a id="jsonDownload" class="hidden rounded-xl bg-surface-container-high border border-outline-variant/20 px-5 py-3 text-sm font-bold text-on-surface hover:bg-surface-container-highest transition-all" href="#">Export JSON</a>
                        <a id="csvDownload" class="hidden rounded-xl bg-surface-container-high border border-outline-variant/20 px-5 py-3 text-sm font-bold text-on-surface hover:bg-surface-container-highest transition-all" href="#">Export CSV</a>
                    </div>
                    
                    <p id="searchMeta" class="mt-4 text-sm font-mono text-primary/80"></p>
                    
                    <div class="mt-8">
                        <div class="grid grid-cols-12 px-4 py-2 text-[10px] font-bold text-on-surface-variant uppercase tracking-wider bg-surface-container-lowest">
                            <div class="col-span-8">Signal Identifiers (Hook & Transcript)</div>
                            <div class="col-span-2 text-center">Metrics</div>
                            <div class="col-span-2 text-right">Platform</div>
                        </div>
                        <div id="searchResults" class="divide-y divide-outline-variant/10 text-sm"></div>
                    </div>
                </div>
            </section>

            <!-- PROFILE TAB -->
            <section id="profileTab" class="tab-section hidden">
                <div class="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_320px]">
                    <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10 shadow-2xl">
                        <h3 class="font-headline text-xl font-bold">Profil Surveillance</h3>
                        <p class="mt-2 text-sm text-on-surface-variant">Analisis pola konten spesifik author TikTok.</p>
                        
                        <div class="mt-6 flex flex-wrap gap-3">
                            <input id="profileInput" class="ds-input p-3 flex-1" placeholder="Masukkan username..." value="openai" />
                            <select id="profileSort" class="ds-input p-3"><option value="latest">Terbaru</option><option value="popular">Popular</option></select>
                            <select id="profileDateRange" class="ds-input p-3"><option value="all">Sepanjang waktu</option><option value="7d">7 hr terakhir</option></select>
                            <button id="profileLoadBtn" class="bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed rounded-xl px-5 py-3 text-sm font-bold hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">Muat Profil</button>
                        </div>
                        <input id="profileFeedSearch" class="ds-input mt-4 w-full p-3" placeholder="Filter di dalam feed profil ini..." />
                        
                        <div class="mt-8">
                            <div id="profileResults" class="divide-y divide-outline-variant/10 text-sm"></div>
                        </div>
                    </div>
                    <div id="profileAnalytics" class="bg-surface-container-high rounded-xl p-6 border border-outline-variant/10 shadow-2xl text-sm text-on-surface-variant flex flex-col justify-center text-center">
                        <span class="material-symbols-outlined text-4xl mb-4 text-outline-variant" style="font-variation-settings:'wght' 200;">monitoring</span>
                        Belum ada data profil.
                    </div>
                </div>
            </section>

            <!-- COMMENTS TAB -->
            <section id="commentsTab" class="tab-section hidden">
                <div class="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_320px]">
                    <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10 shadow-2xl">
                        <h3 class="font-headline text-xl font-bold">Komentar Intel</h3>
                        <p class="mt-2 text-sm text-on-surface-variant">Ambil komentar dari video TikTok untuk menemukan CTA dan respon audiens.</p>
                        
                        <div class="mt-6 grid gap-3 lg:grid-cols-[1fr_120px_160px]">
                            <input id="commentsUrl" class="ds-input p-3" value="https://www.tiktok.com/@openai/video/7604654293966146829" />
                            <input id="commentsMax" type="number" class="ds-input p-3" value="5" />
                            <button id="commentsLoadBtn" class="bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed rounded-xl px-5 py-3 text-sm font-bold hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">Ekstrak</button>
                        </div>
                        <p id="commentsMeta" class="mt-4 text-sm font-mono text-primary/80"></p>
                        <div id="commentsResults" class="mt-6 grid gap-3"></div>
                    </div>
                    <div class="bg-surface-container-high rounded-xl p-6 border border-outline-variant/10 shadow-2xl">
                        <h4 class="font-headline text-lg font-bold text-primary">Comment Intelligence Log</h4>
                        <div class="mt-4 font-mono text-[9px] text-on-surface-variant space-y-2 bg-surface-container-lowest p-3 rounded border border-outline-variant/10">
                            <p>> Menunggu kueri URL...</p>
                            <p>> Parsing komentar dapat memakan waktu, status akan muncul disini.</p>
                            <p class="animate-pulse">_</p>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    </main>

    <!-- Right Side Terminal Panel (Always visible on large screens) -->
    <aside class="hidden xl:flex w-80 bg-[#0c0e12] border-l border-outline-variant/10 p-6 flex flex-col gap-6 overflow-y-auto custom-scrollbar">
        <div class="space-y-4">
            <div class="flex justify-between items-center">
                <h3 class="text-xs font-black uppercase tracking-widest text-on-surface-variant">Intel Summary</h3>
                <span class="text-[10px] font-bold text-primary">AUTO-REFRESH: ON</span>
            </div>
            <div class="p-4 bg-surface-container-low rounded-xl border-l-4 border-primary shadow-soft">
                <p class="text-xs font-bold mb-1 text-on-surface">Critical Divergence</p>
                <p class="text-[11px] text-on-surface-variant leading-relaxed">
                    TikTok engagement for <span class="text-on-surface font-bold">#FinanceTok</span> has dropped 22% in the last 4 hours. Market is shifting toward "Long-form authenticity."
                </p>
            </div>
        </div>

        <div class="space-y-4">
            <h3 class="text-xs font-black uppercase tracking-widest text-on-surface-variant">Top Performers</h3>
            <div class="flex items-center gap-3 p-3 bg-surface-container-high rounded-lg hover:bg-surface-container-highest transition-colors cursor-pointer group">
                <img class="w-10 h-10 rounded-full object-cover grayscale group-hover:grayscale-0 transition-all" src="https://lh3.googleusercontent.com/aida-public/AB6AXuAX8lcs-cmEbZYFsCyZh-NtHPOTePlFHRVjhKGEWLSq-bCGyKrMZFeYuk8MwmRrRMCny6MHWJx-kpOFdrKXaZj2hjW3fmdCMB08FYPvgzlYeGRD3q35OT35zwnO7KDvalOAP_l-9_RAnIYuX3RIwiSSPqkno7EKsiQkQ_Px_kUXlBXy_j8I2-aRva9VlwSF-Ly994P0v5axqx3KiewsQGclaNDv-mnGFKScLwbMBA2xV1R7qBgVLjD3GBaK4MKsxoJU7km7kBYYvw" />
                <div>
                    <p class="text-xs font-bold text-on-surface">Alex Volkov</p>
                    <p class="text-[10px] text-on-surface-variant">Tech Insight • 2.4M Subs</p>
                </div>
                <span class="material-symbols-outlined ml-auto text-primary text-sm">trending_up</span>
            </div>
            <div class="flex items-center gap-3 p-3 bg-surface-container-high rounded-lg hover:bg-surface-container-highest transition-colors cursor-pointer group">
                <img class="w-10 h-10 rounded-full object-cover grayscale group-hover:grayscale-0 transition-all" src="https://lh3.googleusercontent.com/aida-public/AB6AXuB-nDLRV8bp-PWwBGOvkcGna8RoZAoXg76refLz_Oogvbm5gBNAvrJHKmt72GIENQ-mwrx-BWsTYooP67nhJreg4XJCQB-SnTUUrTp6UgwDWSevQHIh6TOb0A7mtzKWp2cQL2fbWjJ72jLbKaXG97hd8WOrXZeYbQXleFGdObIadoSVdWYDIPTfGzjkzvJoVnL-Xo9CpTyChJgZQc6r_kFLKvKakAky0qnz1kDsXm5dk3DFwDn4dkQrxAvjPYG-foYJsanqKwjhyg" />
                <div>
                    <p class="text-xs font-bold text-on-surface">Elena Thorne</p>
                    <p class="text-[10px] text-on-surface-variant">Lifestyle • 890k Subs</p>
                </div>
                <span class="material-symbols-outlined ml-auto text-primary text-sm">trending_up</span>
            </div>
        </div>

        <div class="mt-auto pt-6 border-t border-outline-variant/10">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xs font-black uppercase tracking-widest text-on-surface-variant">Terminal Log</h3>
            </div>
            <div class="font-mono text-[9px] text-on-surface-variant space-y-1 bg-surface-container-lowest p-3 rounded border border-outline-variant/10">
                <p class="text-green-500/70">> Syncing with API...</p>
                <p>> Analysis engine online.</p>
                <p class="text-primary/70">> Ready for hooks extraction.</p>
                <p class="animate-pulse">_</p>
            </div>
        </div>
    </aside>
</div>

<script>
    const sections = {
      dashboard: document.getElementById('dashboardTab'),
      search: document.getElementById('searchTab'),
      profile: document.getElementById('profileTab'),
      comments: document.getElementById('commentsTab')
    };

    function switchTab(name, el){
      document.querySelectorAll('.tab-btn').forEach(b=>{
          b.classList.remove('active');
          if(b.classList.contains('border-r-4')){
              b.classList.remove('border-[#e67e22]');
              b.classList.add('border-transparent');
          }
      });
      el.classList.add('active');
      el.classList.remove('border-transparent');
      el.classList.add('border-[#e67e22]');
      Object.entries(sections).forEach(([k,v])=> v.classList.toggle('hidden', k !== name));
    }

    function escapeHTML(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function rowResult(item) {
      return `<div class="grid grid-cols-12 items-center px-4 py-4 hover:bg-surface-container-high transition-colors group border-b border-outline-variant/10">
        <div class="col-span-8 flex flex-col gap-1 overflow-hidden pr-2">
            <h5 class="text-sm font-bold text-on-surface truncate group-hover:text-primary transition-colors">${escapeHTML(item.hook || item.title || item.caption || 'Tanpa judul')}</h5>
            <p class="text-[10px] text-on-surface-variant truncate">${escapeHTML(item.caption || item.content || item.transcript || '')}</p>
        </div>
        <div class="col-span-2 text-center text-xs font-bold text-on-surface-variant">
            ${escapeHTML(item.views || 0)} views
        </div>
        <div class="col-span-2 text-right flex items-center justify-end gap-2">
            <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-surface-container-highest text-on-surface-variant">${escapeHTML(item.platform || 'N/A')}</span>
        </div>
      </div>`;
    }

    async function runSearch(){
      const q = document.getElementById('keywordInput').value.trim();
      const platform = document.getElementById('platformSelect').value;
      const sort = document.getElementById('sortBy').value;
      const dateRange = document.getElementById('dateRange').value;
      const minViews = document.getElementById('minViews').value;
      const maxViews = document.getElementById('maxViews').value;
      const minLikes = document.getElementById('minLikes').value;
      const maxLikes = document.getElementById('maxLikes').value;
      
      const params = new URLSearchParams({ keyword: q, platforms: platform, max_results: '5', sort, date_range: dateRange });
      if(minViews) params.set('min_views', minViews);
      if(maxViews) params.set('max_views', maxViews);
      if(minLikes) params.set('min_likes', minLikes);
      if(maxLikes) params.set('max_likes', maxLikes);
      
      document.getElementById('searchMeta').textContent = '> Menjalankan kueri ke server...';
      try {
          const res = await fetch('/api/search?' + params.toString());
          const data = await res.json();
          document.getElementById('searchMeta').textContent = `> SUCCESS: ${data.results?.length || 0} hasil ditangkap.`;
          document.getElementById('searchResults').innerHTML = (data.results || []).map(rowResult).join('') || '<div class="p-4 text-sm text-on-surface-variant">Belum ada hasil.</div>';
          
          const jsonLink = document.getElementById('jsonDownload');
          const csvLink = document.getElementById('csvDownload');
          if(data.json_file){ jsonLink.href = '/api/download?file=' + encodeURIComponent(data.json_file); jsonLink.classList.remove('hidden'); }
          if(data.csv_file){ csvLink.href = '/api/download?file=' + encodeURIComponent(data.csv_file); csvLink.classList.remove('hidden'); }
      } catch (err) {
          document.getElementById('searchMeta').textContent = `> ERROR: Gagal memuat data.`;
      }
    }

    async function loadProfile(){
      const username = document.getElementById('profileInput').value.trim();
      const sort = document.getElementById('profileSort').value;
      const dateRange = document.getElementById('profileDateRange').value;
      
      try {
          const res = await fetch(`/api/profile?username=${encodeURIComponent(username)}&max_results=5&sort=${encodeURIComponent(sort)}&date_range=${encodeURIComponent(dateRange)}`);
          if(!res.ok) throw new Error("Gagal mengambil profil.");
          const data = await res.json();
          const results = data.results || [];
          
          document.getElementById('profileResults').innerHTML = results.map(rowResult).join('') || '<div class="p-4 text-sm text-on-surface-variant">Belum ada hasil profil.</div>';
          document.getElementById('profileAnalytics').innerHTML = `<h4 class="font-headline text-lg font-bold mb-3 text-primary">Intelligence Summary</h4><p class="text-sm text-on-surface-variant">${results.length} konten dianalisis dari @<span class="font-bold text-on-surface">${escapeHTML(username)}</span>. Pattern siap disalin.</p>`;
      } catch(err) {
          document.getElementById('profileAnalytics').innerHTML = `<p class="text-error">${err.message}</p>`;
      }
    }

    async function loadComments(){
      const videoUrl = document.getElementById('commentsUrl').value.trim();
      const max = document.getElementById('commentsMax').value || '3';
      
      document.getElementById('commentsMeta').textContent = '> Menghubungkan node ekstraksi komentar...';
      try {
          const res = await fetch(`/api/comments?video_url=${encodeURIComponent(videoUrl)}&max_comments=${encodeURIComponent(max)}`);
          const data = await res.json();
          document.getElementById('commentsMeta').textContent = `> SUCCESS: Diekstrak ${data.total || 0} komentar. Total real count: ${data.video_comment_count ?? '-'}`;
          
          document.getElementById('commentsResults').innerHTML = (data.comments || []).map(c => `
          <div class="bg-surface-container-highest rounded-lg p-4 border-l-2 border-primary">
              <div class="font-bold text-xs text-primary mb-1">${escapeHTML(c.nickname || c.user || 'User')}</div>
              <p class="mt-1 text-sm text-on-surface leading-snug">${escapeHTML(c.text || '')}</p>
          </div>`).join('') || '<div class="text-sm text-on-surface-variant">Belum ada komentar.</div>';
      } catch(err) {
          document.getElementById('commentsMeta').textContent = `> ERROR: Ekstraksi gagal.`;
      }
    }

    document.getElementById('searchBtn').addEventListener('click', runSearch);
    document.getElementById('profileLoadBtn').addEventListener('click', loadProfile);
    document.getElementById('commentsLoadBtn').addEventListener('click', loadComments);
</script>
</body>
</html>
"""

import pathlib
pathlib.Path('app_v2.html').write_text(html_content, encoding='utf-8')
