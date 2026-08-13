# MainPanel LiveData Observer Fixes

File changed: `app/src/main/java/com/atakmap/android/weather2/uipanel/MainPanel.java`

## Root cause

`LiveQueue<T>` extends `androidx.lifecycle.MutableLiveData<T>`. `observeForever(Observer)` /
`removeObserver(Observer)` match observers **by object identity**. `MainPanel` was registering
observers with a bare method reference (`this::observeLiveDataQueue`, `this::gpsToLocObserver`,
`this::mapItemToLocObserver`) and later trying to unregister with a *new* method reference to the
same method. Each occurrence of `this::method` in source generates a distinct synthetic object, so
the "same" reference used at two different call sites is actually two different objects —
`removeObserver` silently failed to find and remove the original observer.

## Changes made

1. Added three `Observer<T>` fields that hold a single stable instance each, reused for both
   register and unregister:
   - `liveDataObserver` (`Observer<EventBase>`) → wraps `observeLiveDataQueue`
   - `gpsToLocObserverRef` (`Observer<WxLocation>`) → wraps `gpsToLocObserver`
   - `mapItemToLocObserverRef` (`Observer<WxLocation>`) → wraps `mapItemToLocObserver`
2. Updated all `observeForever`/`removeObserver` call sites to use these fields instead of raw
   method references.
3. Moved `gpsToLocQueue.observeForever(...)` inside the `if (gpsToLocQueue == null)` block so the
   observer is registered once when the queue is created, not once per GPS location update.
4. Added an early `if (wx == null) return;` guard at the top of `observeLiveDataQueue` (defensive
   fix for the crash itself), and removed the now-redundant duplicate null check further down in
   the method.

## Bug 1 — crash: NullPointerException in `observeLiveDataQueue`

**Feature impacted:** Main weather panel (`MainPanel`) — affects the whole plugin, since
`WxDataHolder.liveDataQueue` is a process-wide singleton, not scoped to one panel instance.

**User flow that caused it:**
1. User opens the weather5 plugin panel → `MainPanel` constructor runs → registers
   `observeLiveDataQueue` via `observeForever` on the singleton `WxDataHolder.liveDataQueue`.
2. User closes/disposes the panel (e.g. closes the plugin, switches away) → `disposeImpl()` runs →
   calls `removeObserver(this::observeLiveDataQueue)`, which — due to the identity bug — does
   **not** remove the real observer, then sets `wx = null`.
3. At any later point, *anything* in the app posts to that singleton queue (data update, settings
   change, network state change) — this can be triggered from completely unrelated code, since the
   panel that "closed" is not really disconnected.
4. The stale observer still fires on the disposed `MainPanel` instance. Its `wx` field is `null` →
   `wx.liveDataQueue.next()` throws NPE on the main thread → app crash.

## Bug 2 — duplicate observers on repeated GPS searches

**Feature impacted:** "Locate me" / GPS-based location search in the weather panel
(`setLocationFromGp` with `locType == GPS`).

**User flow that caused it:**
1. User repeatedly triggers a GPS-based location lookup (e.g. taps "use my location" multiple
   times, or it's triggered repeatedly by location updates).
2. Each call re-ran `gpsToLocQueue.observeForever(this::gpsToLocObserver)` with a *new* observer
   instance — since `gpsToLocQueue` itself is reused (only created once), every call added another
   distinct observer on top of the previous ones instead of reusing/replacing it.
3. Result: a single posted GPS-to-location conversion fires `gpsToLocObserver` once per
   accumulated observer — causing repeated `wx.setLocation`/`wx.loadWxData`/`refreshPanels` calls
   and multiple `gpsToLocQueue.next()` calls per update, which can over-drain the queue and skip or
   duplicate-process queued location conversions. Symptom would be redundant panel refreshes or
   flickering/incorrect weather data after repeated GPS lookups, worsening the more times the
   feature is used in a session.

## Bug 3 — ineffective observer removal (minor, non-crashing)

**Feature impacted:** Map-item location search (tap a map item to fetch weather at that location),
`setLocationFromGp` with `locType == MAP_ITEM`.

**User flow:**
1. User taps a map item to look up weather at that location → a fresh `locLiveQueue` is created
   and `mapItemToLocObserver` registered on it via `observeForever`.
2. When the conversion completes, `mapItemToLocObserver` tries to remove itself via
   `removeObserver(this::mapItemToLocObserver)` — same identity bug, so the removal is a no-op.
3. Low real-world impact here because `locLiveQueue` is a brand-new object each time and is
   immediately dropped (`locLiveQueue = null`) right after, so the whole queue (with its dangling
   observer) becomes garbage-collectable anyway. Fixed for correctness/consistency with the other
   two observers, not because it was causing an observed symptom.
