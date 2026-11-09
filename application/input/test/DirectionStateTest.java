package com.iwin.input;

import org.junit.Test;
import java.util.EnumSet;
import java.util.Collections;
import static org.junit.Assert.*;

public class DirectionStateTest {
    @Test public void releasingRemoteDoesNotReleaseLocal() {
        DirectionState state = new DirectionState();
        state.update("local", EnumSet.of(Direction.UP));
        assertEquals(EnumSet.of(Direction.UP, Direction.RIGHT),
                state.update("remote", EnumSet.of(Direction.RIGHT)));
        assertEquals(EnumSet.of(Direction.UP), state.update("remote", Collections.emptySet()));
    }
    @Test public void callerCannotMutateStoredState() {
        DirectionState state = new DirectionState();
        EnumSet<Direction> original = EnumSet.of(Direction.LEFT);
        state.update("local", original);
        original.clear();
        assertEquals(EnumSet.of(Direction.LEFT), state.snapshot());
    }
}
