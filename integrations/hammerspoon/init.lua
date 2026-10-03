-- Free Mac Voice HUD & Window Integration for Hammerspoon
-- Provides an on-screen floating HUD (pill/banner) and multi-display window tools

local ipc = require("hs.ipc")

-- Auto-reload Hammerspoon config on edit
hs.pathwatcher.new(os.getenv("HOME") .. "/.hammerspoon/", function(files)
    for _, file in pairs(files) do
        if file:sub(-4) == ".lua" then
            hs.reload()
            return
        end
    end
end):start()

--------------------------------------------------------------------------------
-- Floating Visual HUD (hs.canvas)
--------------------------------------------------------------------------------
local hud = nil
local hudTimer = nil

local function dismissHud()
    if hudTimer then
        hudTimer:stop()
        hudTimer = nil
    end
    if hud then
        hud:delete(0.2)
        hud = nil
    end
end

function showVoiceHud(text, state)
    dismissHud()
    if not text or text == "" then
        return
    end

    state = state or "action"
    local screen = hs.screen.mainScreen()
    local sf = screen:frame()

    -- Calculate pill sizing
    local textLen = #text
    local pillW = math.max(260, math.min(640, textLen * 9.5 + 64))
    local pillH = 40
    local pillX = sf.x + (sf.w - pillW) / 2
    local pillY = sf.y + 36 -- Just below standard menu bar

    -- Style based on state
    local icon = "⚡"
    local bgColor = { red = 0.12, green = 0.13, blue = 0.15, alpha = 0.94 }
    local strokeColor = { white = 1.0, alpha = 0.20 }

    if state == "listening" then
        icon = "🎙️"
        bgColor = { red = 0.08, green = 0.14, blue = 0.25, alpha = 0.94 }
        strokeColor = { red = 0.35, green = 0.65, blue = 1.0, alpha = 0.45 }
    elseif state == "transcribed" then
        icon = "💬"
        bgColor = { red = 0.10, green = 0.16, blue = 0.22, alpha = 0.94 }
        strokeColor = { red = 0.40, green = 0.70, blue = 0.95, alpha = 0.35 }
    elseif state == "rejected" then
        icon = "💤"
        bgColor = { red = 0.18, green = 0.18, blue = 0.20, alpha = 0.88 }
        strokeColor = { white = 1.0, alpha = 0.12 }
    elseif state == "error" then
        icon = "⚠️"
        bgColor = { red = 0.25, green = 0.09, blue = 0.09, alpha = 0.94 }
        strokeColor = { red = 1.0, green = 0.35, blue = 0.35, alpha = 0.45 }
    elseif state == "success" or state == "action" then
        icon = "⚡"
        bgColor = { red = 0.08, green = 0.18, blue = 0.12, alpha = 0.94 }
        strokeColor = { red = 0.35, green = 0.85, blue = 0.45, alpha = 0.40 }
    end

    hud = hs.canvas.new({ x = pillX, y = pillY, w = pillW, h = pillH })
    hud:level(hs.canvas.windowLevels.overlay)
    hud:behavior(hs.canvas.windowBehaviors.canJoinAllSpaces)
    hud:clickActivating(false)

    -- 1. Rounded Pill Background
    hud[1] = {
        type = "rectangle",
        action = "fill",
        roundedRectRadii = { xRadius = 12, yRadius = 12 },
        fillColor = bgColor,
    }

    -- 2. Sleek Border
    hud[2] = {
        type = "rectangle",
        action = "stroke",
        roundedRectRadii = { xRadius = 12, yRadius = 12 },
        strokeColor = strokeColor,
        strokeWidth = 1,
    }

    -- 3. Label (Icon + Text)
    hud[3] = {
        type = "text",
        text = icon .. "  " .. text,
        textFont = ".AppleSystemUIFont",
        textSize = 13.5,
        textColor = { white = 0.96, alpha = 1.0 },
        textAlignment = "center",
        frame = { x = 8, y = 10, w = pillW - 16, h = 22 },
    }

    hud:show(0.12)

    local duration = (state == "listening") and 3.5 or 1.8
    hudTimer = hs.timer.doAfter(duration, function()
        dismissHud()
    end)
end

--------------------------------------------------------------------------------
-- Multi-Display Window Routing
--------------------------------------------------------------------------------
function moveFocusedWindowToNextScreen()
    local win = hs.window.focusedWindow()
    if not win then return false end
    local screen = win:screen()
    local nextScreen = screen:next()
    if not nextScreen or nextScreen == screen then
        showVoiceHud("Only one display connected", "error")
        return false
    end
    win:moveToScreen(nextScreen)
    showVoiceHud("Moved to " .. (nextScreen:name() or "next display"), "action")
    return true
end

function moveFocusedWindowToScreenName(screenName)
    local win = hs.window.focusedWindow()
    if not win then return false end
    local target = hs.screen.find(screenName)
    if not target then
        showVoiceHud("Display not found: " .. screenName, "error")
        return false
    end
    win:moveToScreen(target)
    showVoiceHud("Moved to " .. (target:name() or screenName), "action")
    return true
end

--------------------------------------------------------------------------------
-- Local HTTP API Server for Free Mac Voice (Port 19825)
--------------------------------------------------------------------------------
local server = hs.httpserver.new(function(method, path, headers, body)
    if path == "/hud" and method == "POST" then
        local ok, data = pcall(hs.json.decode, body or "{}")
        if ok and data and data.text then
            showVoiceHud(data.text, data.state or "action")
            return "OK\n", 200, {["Content-Type"] = "text/plain"}
        end
        return "Bad Request\n", 400, {["Content-Type"] = "text/plain"}
    elseif path == "/display/next" and method == "POST" then
        local success = moveFocusedWindowToNextScreen()
        return (success and "OK\n" or "Failed\n"), 200, {["Content-Type"] = "text/plain"}
    elseif path == "/status" then
        return hs.json.encode({status = "ok", app = "hammerspoon", service = "free-mac-voice"}), 200, {["Content-Type"] = "application/json"}
    end
    return "Not Found\n", 404, {["Content-Type"] = "text/plain"}
end)

server:setPort(19825)
server:setInterface("localhost")
server:start()

hs.notify.new({
    title = "Free Mac Voice",
    informativeText = "Hammerspoon HUD & Display server started on port 19825",
}):send()

print("Free Mac Voice Hammerspoon server active on port 19825")
