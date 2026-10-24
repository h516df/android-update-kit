package dev.h516.update

import java.util.concurrent.Future

/** One operation owns one connection and one worker; cancellation is never reset. */
internal class UpdateRun(val http: UpdateHttp) {
    @Volatile private var stopped = false
    @Volatile private var work: Future<*>? = null
    val isCancelled: Boolean get() = stopped

    fun attach(task: Future<*>) {
        work = task
        if (stopped) task.cancel(true)
    }

    fun ensureAlive() {
        if (stopped || Thread.currentThread().isInterrupted) throw InterruptedException()
    }

    fun cancel() {
        stopped = true
        work?.cancel(true)
        http.cancel()
    }
}

/** The updater's current operation, including a guard for queued UI callbacks. */
internal class UpdateRuns {
    @Volatile var current: UpdateRun? = null
        private set

    @Synchronized fun start(http: UpdateHttp = UpdateHttp()): UpdateRun {
        val previous = current
        val next = UpdateRun(http)
        current = next
        previous?.cancel()
        return next
    }

    @Synchronized fun cancel() {
        val previous = current
        current = null
        previous?.cancel()
    }

    fun isCurrent(run: UpdateRun?): Boolean = current === run && run?.isCancelled != true

    @Synchronized fun <T> withCurrent(run: UpdateRun, action: () -> T): T {
        run.ensureAlive()
        if (!isCurrent(run)) throw InterruptedException()
        return action()
    }
}
