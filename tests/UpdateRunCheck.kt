package dev.h516.update

import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

private fun interrupted(action: () -> Unit) {
    try {
        action()
        error("A cancelled operation continued")
    } catch (_: InterruptedException) {}
}

fun main() {
    var requests = 0
    fun http() = UpdateHttp { url ->
        requests++
        object : HttpURLConnection(URL(url)) {
            override fun connect() {}
            override fun disconnect() {}
            override fun usingProxy() = false
            override fun getResponseCode() = 200
            override fun getInputStream() = ByteArrayInputStream("[]".toByteArray())
        }
    }
    val runs = UpdateRuns()
    val first = runs.start(http())
    check(first.http.json("/releases", null) == "[]")
    val lateStates = mutableListOf<String>()
    val staleCallback = { if (runs.isCurrent(first)) lateStates += "old result" }
    runs.cancel()
    check(runs.current == null)
    interrupted { first.http.json("/releases", null) }
    interrupted { first.ensureAlive() }
    check(requests == 1)
    val staleIdle = { if (runs.isCurrent(null)) lateStates += "cancelled idle" }
    val next = runs.start(http())
    check(next.http.json("/releases", null) == "[]")
    check(requests == 2 && runs.isCurrent(next))
    staleCallback()
    staleIdle()
    check(lateStates.isEmpty())
    var staleSave = false
    interrupted { runs.withCurrent(first) { staleSave = true } }
    check(!staleSave)
    runs.withCurrent(next) { check(!next.isCancelled) }

    val worker = Executors.newSingleThreadExecutor()
    try {
        val sleeping = runs.start(http())
        val entered = CountDownLatch(1)
        val stopped = CountDownLatch(1)
        val future = worker.submit {
            entered.countDown()
            try { Thread.sleep(30_000) } catch (_: InterruptedException) { stopped.countDown() }
        }
        sleeping.attach(future)
        check(entered.await(2, TimeUnit.SECONDS))
        runs.cancel()
        check(stopped.await(2, TimeUnit.SECONDS) && future.isCancelled)
        val retry = runs.start(http())
        val retryFuture = worker.submit<String> { retry.ensureAlive(); retry.http.json("/releases", null) }
        retry.attach(retryFuture)
        check(retryFuture.get(2, TimeUnit.SECONDS) == "[]")
        sleeping.cancel()
        check(runs.isCurrent(retry) && !retry.isCancelled)

        val cancelledBeforeAttach = runs.start(http())
        runs.cancel()
        val queued = java.util.concurrent.FutureTask<Unit> { error("Cancelled queued work ran") }
        cancelledBeforeAttach.attach(queued)
        check(queued.isCancelled)
    } finally {
        runs.cancel()
        worker.shutdownNow()
    }
    println("Cancel, retry, worker interruption and stale-result checks passed")
}
